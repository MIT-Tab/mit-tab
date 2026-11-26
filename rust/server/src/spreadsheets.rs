use std::{collections::BTreeMap, io::Cursor};

use excelerate::{
    model::{CellValue, Spreadsheet, Worksheet},
    reader::{xls::read_xls_from, xlsx::read_xlsx_from_limited},
    writer::{write_xls_to, write_xlsx_to},
};

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Format {
    Csv,
    Xls,
    Xlsx,
}

impl Format {
    pub fn parse(extension: &str) -> Option<Self> {
        match extension.to_ascii_lowercase().as_str() {
            "csv" => Some(Self::Csv),
            "xls" => Some(Self::Xls),
            "xlsx" => Some(Self::Xlsx),
            _ => None,
        }
    }

    pub fn suffix(self) -> &'static str {
        match self {
            Self::Csv => "csv",
            Self::Xls => "xls",
            Self::Xlsx => "xlsx",
        }
    }

    pub fn content_type(self) -> &'static str {
        match self {
            Self::Csv => "text/csv; charset=utf-8",
            Self::Xls => "application/vnd.ms-excel",
            Self::Xlsx => "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
    }
}

pub type Rows = Vec<(usize, Vec<String>)>;

pub fn read(format: Format, input: &[u8]) -> Result<Rows, &'static str> {
    const UNREADABLE: &str =
        "Could not read the spreadsheet. Open it in a spreadsheet editor and save it again.";
    let rows: Rows = match format {
        Format::Csv => {
            let text = std::str::from_utf8(input)
                .map_err(|_| "Could not decode the CSV. Save it as UTF-8.")?;
            let text = text.strip_prefix('\u{feff}').unwrap_or(text);
            if text.contains('\0') {
                return Err("The CSV contains binary data.");
            }
            let mut csv = csv::ReaderBuilder::new()
                .has_headers(false)
                .flexible(true)
                .from_reader(text.as_bytes());
            csv.records()
                .enumerate()
                .map(|(index, record)| {
                    let values = record
                        .map_err(|_| UNREADABLE)?
                        .iter()
                        .map(str::trim)
                        .map(str::to_owned)
                        .collect();
                    Ok::<_, &'static str>((index + 1, values))
                })
                .collect::<Result<_, _>>()?
        }
        Format::Xls | Format::Xlsx => {
            let file = match format {
                Format::Xls => read_xls_from(input),
                Format::Xlsx => read_xlsx_from_limited(Cursor::new(input), 32 * 1024 * 1024),
                Format::Csv => unreachable!(),
            }
            .map_err(|_| UNREADABLE)?;
            let sheet = file.sheet(0).ok_or("The spreadsheet has no sheets.")?;
            let mut cells = BTreeMap::<usize, Vec<String>>::new();
            for (address, cell) in sheet.iter() {
                let value = text(&cell.value);
                if value.trim().is_empty() {
                    continue;
                }
                let row = address.row.one_based() as usize;
                let column = address.col.index() as usize;
                if column >= 256 || row > 100_000 {
                    return Err("The spreadsheet exceeds the row or column limit.");
                }
                let entry = cells.entry(row).or_default();
                if entry.len() <= column {
                    entry.resize(column + 1, String::new());
                }
                entry[column] = value.trim().into();
            }
            cells.into_iter().collect()
        }
    };
    let rows: Rows = rows
        .into_iter()
        .filter(|(_, values)| values.iter().any(|value| !value.is_empty()))
        .collect();
    if rows.is_empty() {
        return Err("The spreadsheet is empty.");
    }
    if rows.len() > 100_000 || rows.iter().any(|(_, cells)| cells.len() > 256) {
        return Err("The spreadsheet exceeds the row or column limit.");
    }
    Ok(rows)
}

fn text(value: &CellValue) -> String {
    match value {
        CellValue::Text(value) => value.to_string(),
        CellValue::Number(number) => number.to_string(),
        CellValue::Bool(value) => {
            if *value {
                "True".into()
            } else {
                "False".into()
            }
        }
        CellValue::RichText(runs) => runs.iter().map(|run| run.text.as_str()).collect(),
        CellValue::Formula {
            cached: Some(result),
            ..
        } => text(result),
        CellValue::Empty | CellValue::Error(_) | CellValue::Formula { .. } => String::new(),
    }
}

pub fn write(
    format: Format,
    headers: &[&str],
    rows: &[Vec<String>],
    byte_order_mark: bool,
) -> Result<Vec<u8>, &'static str> {
    const ERROR: &str = "Could not write the spreadsheet. Check the cell text and file size.";
    if format == Format::Csv {
        let mut data = if byte_order_mark {
            vec![0xef, 0xbb, 0xbf]
        } else {
            Vec::new()
        };
        {
            let mut writer = csv::WriterBuilder::new()
                .terminator(csv::Terminator::CRLF)
                .from_writer(&mut data);
            writer.write_record(headers).map_err(|_| ERROR)?;
            for row in rows {
                writer.write_record(row).map_err(|_| ERROR)?;
            }
            writer.flush().map_err(|_| ERROR)?;
        }
        return Ok(data);
    }
    let mut workbook = Spreadsheet::empty();
    let mut sheet = Worksheet::new("Data").map_err(|_| ERROR)?;
    for (column, header) in headers.iter().enumerate() {
        sheet
            .set_at(1, column as u32 + 1, *header)
            .map_err(|_| ERROR)?;
    }
    for (index, row) in rows.iter().enumerate() {
        for (column, value) in row.iter().enumerate() {
            if value.encode_utf16().count() > 32_767 || value.contains('\0') {
                return Err(ERROR);
            }
            sheet
                .set_at(index as u32 + 2, column as u32 + 1, value.as_str())
                .map_err(|_| ERROR)?;
        }
    }
    workbook.add_sheet(sheet).map_err(|_| ERROR)?;
    let mut output = Vec::new();
    if format == Format::Xls {
        if rows.len() >= 65_536 || headers.len() > 256 {
            return Err(ERROR);
        }
        write_xls_to(&workbook, &mut output).map_err(|_| ERROR)?;
    } else {
        write_xlsx_to(&workbook, Cursor::new(&mut output)).map_err(|_| ERROR)?;
    }
    Ok(output)
}

pub fn normalized_header(value: &str) -> String {
    value
        .split('(')
        .next()
        .unwrap_or_default()
        .to_ascii_lowercase()
        .chars()
        .filter(char::is_ascii_alphanumeric)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn csv_and_excel_preserve_unicode_and_literal_formula_text() {
        let rows = vec![
            vec!["École, “Example”".into(), "-1".into()],
            vec!["=1+2".into(), "17".into()],
        ];
        for format in [Format::Csv, Format::Xls, Format::Xlsx] {
            let file = write(format, &["school", "id"], &rows, true).unwrap();
            let read = read(format, &file).unwrap();
            assert_eq!(read[0], (1, vec!["school".into(), "id".into()]));
            assert_eq!(read[1].1, rows[0]);
            assert_eq!(read[2].1, rows[1]);
        }
    }
}
