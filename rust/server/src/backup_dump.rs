use std::collections::BTreeSet;

use crate::schema;

// Restoring data against the installed schema keeps DDL, credentials, and
// server configuration outside a backup's execution surface.
pub struct Dump<'a> {
    pub tables: BTreeSet<&'a str>,
    pub inserts: Vec<Insert<'a>>,
}

pub struct Insert<'a> {
    pub table: &'a str,
    pub sql: &'a str,
}

pub const INVALID: &str = "This file is not a supported MIT-Tab MySQL dump.";

pub fn parse(bytes: &[u8]) -> Result<Dump<'_>, &'static str> {
    let text = std::str::from_utf8(bytes).map_err(|_| INVALID)?;
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    if text.contains('\0')
        || !text
            .lines()
            .rev()
            .map(str::trim)
            .find(|line| !line.is_empty())
            .is_some_and(|line| line.starts_with("-- Dump completed on "))
    {
        return Err(INVALID);
    }
    let mut dump = Dump {
        tables: BTreeSet::new(),
        inserts: Vec::new(),
    };
    let mut cursor = 0;
    while skip_prefix(text.as_bytes(), &mut cursor)? {
        let end = end_of_statement(text.as_bytes(), cursor)?;
        let sql = text[cursor..end].trim();
        cursor = end + 1;
        if sql.is_empty() {
            continue;
        }
        match classify(sql)? {
            Item::Table(table) if schema::backup_table(table) => {
                dump.tables.insert(table);
            }
            Item::Insert(table) if schema::backup_table(table) => {
                dump.tables.insert(table);
                if !schema::temporary_backup_table(table) {
                    dump.inserts.push(Insert { table, sql });
                }
            }
            _ => {}
        }
    }
    for required in [
        "auth_user",
        "auth_user_groups",
        "tab_tabsettings",
        "tab_school",
        "tab_team",
        "tab_debater",
        "tab_team_debaters",
        "tab_judge",
        "tab_room",
        "tab_round",
        "tab_outround",
    ] {
        if !dump.tables.contains(required) {
            return Err(INVALID);
        }
    }
    Ok(dump)
}

enum Item<'a> {
    Table(&'a str),
    Insert(&'a str),
    Ignore,
}

fn classify(sql: &str) -> Result<Item<'_>, &'static str> {
    let mut input = Input {
        text: sql,
        position: 0,
    };
    if input.keyword("CREATE") {
        input.require("TABLE")?;
        if input.keyword("IF") {
            input.require("NOT")?;
            input.require("EXISTS")?;
        }
        let table = input.ident()?;
        input.punct(b'(')?;
        return Ok(Item::Table(table));
    }
    if input.keyword("INSERT") {
        input.require("INTO")?;
        let table = input.ident()?;
        input.punct(b'(')?;
        if !schema::backup_table(table) {
            // A dump can have tables from a separately installed Django
            // extension. Ignore their data without sending it to MySQL.
            return Ok(Item::Ignore);
        }
        let mut columns = BTreeSet::new();
        loop {
            if !columns.insert(input.ident()?) {
                return Err(INVALID);
            }
            if !input.take(b',') {
                break;
            }
        }
        input.punct(b')')?;
        input.require("VALUES")?;
        loop {
            input.punct(b'(')?;
            for index in 0..columns.len() {
                if index != 0 {
                    input.punct(b',')?;
                }
                input.literal()?;
            }
            input.punct(b')')?;
            if !input.take(b',') {
                break;
            }
        }
        input.space();
        if input.position != sql.len() {
            return Err(INVALID);
        }
        return Ok(Item::Insert(table));
    }
    if ["DROP", "ALTER", "LOCK", "UNLOCK", "SET"]
        .into_iter()
        .any(|word| input.keyword(word))
    {
        return Ok(Item::Ignore);
    }
    Err(INVALID)
}

fn skip_prefix(bytes: &[u8], index: &mut usize) -> Result<bool, &'static str> {
    while *index < bytes.len() {
        match bytes[*index] {
            value if value.is_ascii_whitespace() || value == b';' => *index += 1,
            b'#' => line(bytes, index),
            b'-' if line_comment(bytes, *index) => line(bytes, index),
            b'/' if bytes.get(*index + 1) == Some(&b'*') => comment(bytes, index)?,
            _ => return Ok(true),
        }
    }
    Ok(false)
}

fn end_of_statement(bytes: &[u8], start: usize) -> Result<usize, &'static str> {
    let mut index = start;
    while let Some(&byte) = bytes.get(index) {
        match byte {
            b';' => return Ok(index),
            b'\'' | b'"' | b'`' => quoted(bytes, &mut index, byte)?,
            b'#' => line(bytes, &mut index),
            b'-' if line_comment(bytes, index) => line(bytes, &mut index),
            b'/' if bytes.get(index + 1) == Some(&b'*') => comment(bytes, &mut index)?,
            _ => index += 1,
        }
    }
    // SQL files produced by mysqldump terminate every statement.
    Err(INVALID)
}

fn line_comment(bytes: &[u8], position: usize) -> bool {
    bytes.get(position + 1) == Some(&b'-')
        && bytes.get(position + 2).is_none_or(u8::is_ascii_whitespace)
}

fn line(bytes: &[u8], cursor: &mut usize) {
    while bytes.get(*cursor).is_some_and(|&byte| byte != b'\n') {
        *cursor += 1;
    }
}

fn comment(bytes: &[u8], cursor: &mut usize) -> Result<(), &'static str> {
    let Some(end) = bytes
        .get(*cursor + 2..)
        .and_then(|tail| tail.windows(2).position(|pair| pair == b"*/"))
    else {
        return Err(INVALID);
    };
    *cursor += end + 4;
    Ok(())
}

fn quoted(bytes: &[u8], cursor: &mut usize, delimiter: u8) -> Result<(), &'static str> {
    *cursor += 1;
    while let Some(&byte) = bytes.get(*cursor) {
        *cursor += 1;
        if byte == b'\\' && delimiter != b'`' {
            if *cursor >= bytes.len() {
                return Err(INVALID);
            }
            *cursor += 1;
        } else if byte == delimiter {
            if bytes.get(*cursor) == Some(&delimiter) {
                *cursor += 1;
            } else {
                return Ok(());
            }
        }
    }
    Err(INVALID)
}

struct Input<'a> {
    text: &'a str,
    position: usize,
}

impl<'a> Input<'a> {
    fn space(&mut self) {
        while self
            .text
            .as_bytes()
            .get(self.position)
            .is_some_and(u8::is_ascii_whitespace)
        {
            self.position += 1;
        }
    }

    fn keyword(&mut self, word: &str) -> bool {
        self.space();
        let end = self.position + word.len();
        if self
            .text
            .as_bytes()
            .get(self.position..end)
            .is_some_and(|actual| actual.eq_ignore_ascii_case(word.as_bytes()))
            && self
                .text
                .as_bytes()
                .get(end)
                .is_none_or(|byte| !identifier(*byte))
        {
            self.position = end;
            true
        } else {
            false
        }
    }

    fn require(&mut self, word: &str) -> Result<(), &'static str> {
        if self.keyword(word) {
            Ok(())
        } else {
            Err(INVALID)
        }
    }

    fn take(&mut self, byte: u8) -> bool {
        self.space();
        if self.text.as_bytes().get(self.position) == Some(&byte) {
            self.position += 1;
            true
        } else {
            false
        }
    }

    fn punct(&mut self, byte: u8) -> Result<(), &'static str> {
        if self.take(byte) {
            Ok(())
        } else {
            Err(INVALID)
        }
    }

    fn ident(&mut self) -> Result<&'a str, &'static str> {
        let quoted = self.take(b'`');
        let start = self.position;
        let bytes = self.text.as_bytes();
        while bytes
            .get(self.position)
            .is_some_and(|&byte| identifier(byte))
        {
            self.position += 1;
        }
        if self.position == start || self.position - start > 64 {
            return Err(INVALID);
        }
        let end = self.position;
        if quoted && bytes.get(self.position) != Some(&b'`') {
            return Err(INVALID);
        }
        self.position += usize::from(quoted);
        Ok(&self.text[start..end])
    }

    fn literal(&mut self) -> Result<(), &'static str> {
        self.space();
        let bytes = self.text.as_bytes();
        if bytes.get(self.position) == Some(&b'\'') {
            return quoted(bytes, &mut self.position, b'\'');
        }
        for prefix in ["_binary", "_utf8mb4", "_utf8mb3", "b", "x"] {
            let start = self.position;
            if self.keyword(prefix) && self.text.as_bytes().get(self.position) == Some(&b'\'') {
                return quoted(bytes, &mut self.position, b'\'');
            }
            self.position = start;
        }
        if ["NULL", "TRUE", "FALSE"]
            .into_iter()
            .any(|word| self.keyword(word))
        {
            return Ok(());
        }
        let start = self.position;
        if bytes
            .get(start..start + 2)
            .is_some_and(|prefix| prefix.eq_ignore_ascii_case(b"0x"))
        {
            self.position += 2;
            let hex = self.position;
            while bytes.get(self.position).is_some_and(u8::is_ascii_hexdigit) {
                self.position += 1;
            }
            if self.position == hex {
                return Err(INVALID);
            }
            return Ok(());
        }
        while bytes.get(self.position).is_some_and(|byte| {
            byte.is_ascii_digit() || matches!(byte, b'+' | b'-' | b'.' | b'e' | b'E')
        }) {
            self.position += 1;
        }
        let token = &self.text[start..self.position];
        if token.parse::<f64>().is_ok_and(f64::is_finite) {
            Ok(())
        } else {
            Err(INVALID)
        }
    }
}

fn identifier(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_'
}

#[cfg(test)]
mod tests {
    use super::*;

    const SCHEMA: &str = r#"
      /*!80000 SET @@GLOBAL.SYSTEM_VARIABLE = 1 */;
      DROP TABLE IF EXISTS tab_tabsettings;
      CREATE TABLE auth_user (`id` INT);
      CREATE TABLE auth_user_groups (`id` INT);
      CREATE TABLE tab_tabsettings (`id` INT);
      CREATE TABLE tab_school (`id` INT);
      CREATE TABLE tab_debater (`id` INT);
      CREATE TABLE tab_team_debaters (`id` INT);
      CREATE TABLE tab_room (`id` INT);
      CREATE TABLE tab_outround (`id` INT);
      CREATE TABLE tab_team (`id` INT);
      CREATE TABLE tab_judge (`id` INT);
      CREATE TABLE tab_round (`id` INT);
      CREATE TABLE third_party_table (`id` INT);
    "#;

    const FOOTER: &str = "\n-- Dump completed on 2026-01-01 00:00:00\n";

    #[test]
    fn reads_literals_without_replaying_mysql_controls() {
        let document = format!(
            "{SCHEMA}INSERT INTO `tab_tabsettings` (`id`, `value_string`) VALUES (1,'semicolon; retained'),(2,'two ''quotes''');{FOOTER}"
        );
        let dump = parse(document.as_bytes()).unwrap();
        assert_eq!(dump.tables.len(), 11);
        assert_eq!(dump.inserts.len(), 1);
        assert!(dump.inserts[0].sql.contains("semicolon; retained"));
    }

    #[test]
    fn rejects_qualified_writes_expressions_and_incomplete_dumps() {
        for suffix in [
            "INSERT INTO elsewhere.tab_team (id) VALUES (1);",
            "INSERT INTO tab_team (id) SELECT 1;",
            "INSERT INTO tab_team (id) VALUES (1 + 2);",
            "INSERT INTO tab_team (id) VALUES (1) ON DUPLICATE KEY UPDATE id = 2;",
            "USE elsewhere;",
            "INSERT INTO tab_team (id) VALUES (1 /* comment */);",
        ] {
            assert!(
                parse(format!("{SCHEMA}{suffix}{FOOTER}").as_bytes()).is_err(),
                "{suffix}"
            );
        }
        assert!(parse(SCHEMA.as_bytes()).is_err());
        let no_members = SCHEMA
            .lines()
            .filter(|line| !line.contains("tab_team_debaters"))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(parse(format!("{no_members}{FOOTER}").as_bytes()).is_err());
    }
}
