use std::collections::HashMap;

use sqlx::{MySql, MySqlConnection, QueryBuilder};

enum Value {
    Missing,
    Integer(i32),
    Text(String),
}

pub struct Settings(HashMap<String, Value>);

impl Settings {
    pub async fn load(db: &mut MySqlConnection, keys: &[&str]) -> Result<Self, sqlx::Error> {
        if keys.is_empty() {
            return Ok(Self(HashMap::new()));
        }
        let mut query = QueryBuilder::<MySql>::new(
            "SELECT `key`, value, value_string FROM tab_tabsettings WHERE `key` IN (",
        );
        let mut fields = query.separated(", ");
        for key in keys {
            fields.push_bind(key);
        }
        query.push(") ORDER BY id");
        let rows = query
            .build_query_as::<(String, Option<i32>, Option<String>)>()
            .fetch_all(db)
            .await?;
        let mut values = HashMap::new();
        for (key, integer, text) in rows {
            match text
                .map(Value::Text)
                .or_else(|| integer.map(Value::Integer))
            {
                Some(value) => {
                    values.entry(key).or_insert(value);
                }
                None => {
                    // A null first row still wins over duplicates of the key.
                    values.entry(key).or_insert(Value::Missing);
                }
            }
        }
        Ok(Self(values))
    }

    pub fn integer(&self, name: &str, default: i32) -> i32 {
        match self.0.get(name) {
            Some(Value::Integer(value)) => *value,
            Some(Value::Text(value)) => value.trim().parse().unwrap_or(default),
            Some(Value::Missing) | None => default,
        }
    }

    pub fn string(&self, name: &str, default: &str) -> String {
        match self.0.get(name) {
            Some(Value::Integer(value)) => value.to_string(),
            Some(Value::Text(value)) => value.clone(),
            Some(Value::Missing) | None => default.to_owned(),
        }
    }

    pub fn flag(&self, name: &str, default: bool) -> bool {
        match self.0.get(name) {
            Some(Value::Integer(value)) => *value != 0,
            Some(Value::Text(value)) => !value.is_empty(),
            Some(Value::Missing) | None => default,
        }
    }
}
