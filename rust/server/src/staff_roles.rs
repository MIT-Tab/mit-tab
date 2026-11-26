use sqlx::MySqlPool;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Preset {
    Scratches,
    ScratchRead,
    Checkin,
    DataEntry,
    Assistant,
}

impl Preset {
    pub fn landing(self) -> &'static str {
        match self {
            Self::Scratches => "/enter_scratch/",
            Self::ScratchRead => "/scratches/view/",
            Self::Checkin => "/batch_checkin/",
            Self::DataEntry | Self::Assistant => "/",
        }
    }

    pub fn checkins(self) -> bool {
        matches!(self, Self::Checkin | Self::Assistant)
    }
    pub fn data_entry(self) -> bool {
        matches!(self, Self::DataEntry | Self::Assistant)
    }
}

pub async fn preset(db: &MySqlPool, user: i32) -> Result<Option<Preset>, sqlx::Error> {
    let role = sqlx::query_scalar::<_, String>(
        "SELECT g.name FROM auth_group g
         JOIN auth_user_groups m ON m.group_id = g.id WHERE m.user_id = ?
           AND g.name IN ('MIT-TAB: Add scratches', 'MIT-TAB: Create and view scratches',
                          'MIT-TAB: Check-in helper', 'MIT-TAB: Data entry helper', 'MIT-TAB: Tab assistant')
         ORDER BY FIELD(g.name, 'MIT-TAB: Add scratches', 'MIT-TAB: Create and view scratches',
                          'MIT-TAB: Check-in helper', 'MIT-TAB: Data entry helper', 'MIT-TAB: Tab assistant')
         LIMIT 1",
    ).bind(user).fetch_optional(db).await?;
    Ok(match role.as_deref() {
        Some("MIT-TAB: Add scratches") => Some(Preset::Scratches),
        Some("MIT-TAB: Create and view scratches") => Some(Preset::ScratchRead),
        Some("MIT-TAB: Check-in helper") => Some(Preset::Checkin),
        Some("MIT-TAB: Data entry helper") => Some(Preset::DataEntry),
        Some("MIT-TAB: Tab assistant") => Some(Preset::Assistant),
        _ => None,
    })
}
