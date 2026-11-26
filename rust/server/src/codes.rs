const ADJECTIVES: &[&str] = &[
    "aged",
    "ancient",
    "autumn",
    "billowing",
    "bitter",
    "black",
    "blue",
    "bold",
    "broad",
    "broken",
    "calm",
    "cold",
    "cool",
    "crimson",
    "curly",
    "damp",
    "dark",
    "dawn",
    "delicate",
    "divine",
    "dry",
    "empty",
    "falling",
    "fancy",
    "flat",
    "floral",
    "fragrant",
    "frosty",
    "gentle",
    "green",
    "hidden",
    "holy",
    "icy",
    "jolly",
    "late",
    "lingering",
    "little",
    "lively",
    "long",
    "lucky",
    "misty",
    "morning",
    "muddy",
    "mute",
    "nameless",
    "noisy",
    "odd",
    "old",
    "orange",
    "patient",
    "plain",
    "polished",
    "proud",
    "purple",
    "quiet",
    "rapid",
    "raspy",
    "red",
    "restless",
    "rough",
    "round",
    "royal",
    "shiny",
    "shrill",
    "shy",
    "silent",
    "small",
    "snowy",
    "soft",
    "solitary",
    "sparkling",
    "spring",
    "square",
    "steep",
    "still",
    "summer",
    "super",
    "sweet",
    "throbbing",
    "tight",
    "tiny",
    "twilight",
    "wandering",
    "weathered",
    "white",
    "wild",
    "winter",
    "wispy",
    "withered",
    "yellow",
    "young",
];

const NOUNS: &[&str] = &[
    "art",
    "band",
    "bar",
    "base",
    "bird",
    "block",
    "boat",
    "bonus",
    "bread",
    "breeze",
    "brook",
    "bush",
    "butterfly",
    "cake",
    "cell",
    "cherry",
    "cloud",
    "credit",
    "darkness",
    "dawn",
    "dew",
    "disk",
    "dream",
    "dust",
    "feather",
    "field",
    "fire",
    "firefly",
    "flower",
    "fog",
    "forest",
    "frog",
    "frost",
    "glade",
    "glitter",
    "grass",
    "hall",
    "hat",
    "haze",
    "heart",
    "hill",
    "king",
    "lab",
    "lake",
    "leaf",
    "limit",
    "math",
    "meadow",
    "mode",
    "moon",
    "morning",
    "mountain",
    "mouse",
    "mud",
    "night",
    "paper",
    "pine",
    "poetry",
    "pond",
    "queen",
    "rain",
    "recipe",
    "resonance",
    "rice",
    "river",
    "salad",
    "scene",
    "sea",
    "shadow",
    "shape",
    "silence",
    "sky",
    "smoke",
    "snow",
    "snowflake",
    "sound",
    "star",
    "sun",
    "sunset",
    "surf",
    "term",
    "thunder",
    "tooth",
    "tree",
    "truth",
    "union",
    "unit",
    "violet",
    "voice",
    "water",
    "waterfall",
    "wave",
    "wildflower",
    "wind",
    "wood",
];
use ring::rand::{SecureRandom, SystemRandom};
use sqlx::MySqlConnection;

#[derive(Clone, Copy)]
pub enum Credential {
    Team,
    Judge,
    Registration,
}

fn draw(random: &SystemRandom, bound: u32) -> Result<usize, sqlx::Error> {
    let limit = u32::MAX - u32::MAX % bound;
    loop {
        let mut bytes = [0; 4];
        random
            .fill(&mut bytes)
            .map_err(|_| sqlx::Error::Protocol("Random source unavailable".into()))?;
        let candidate = u32::from_ne_bytes(bytes);
        if candidate < limit {
            return Ok((candidate % bound) as usize);
        }
    }
}

pub async fn unique(db: &mut MySqlConnection, kind: Credential) -> Result<String, sqlx::Error> {
    let query = match kind {
        Credential::Team => "SELECT EXISTS(SELECT 1 FROM tab_team WHERE team_code = ?)",
        Credential::Judge => "SELECT EXISTS(SELECT 1 FROM tab_judge WHERE ballot_code = ?)",
        Credential::Registration => {
            "SELECT EXISTS(SELECT 1 FROM registration_registration WHERE herokunator_code = ?)"
        }
    };
    let alphabet = b"23456789abcdefghijkmnopqrstuvwxyz";
    let random = SystemRandom::new();
    for _ in 0..64 {
        let a = ADJECTIVES[draw(&random, ADJECTIVES.len() as u32)?];
        let n = NOUNS[draw(&random, NOUNS.len() as u32)?];
        let mut code = format!("{a}-{n}-");
        for _ in 0..6 {
            code.push(char::from(alphabet[draw(&random, alphabet.len() as u32)?]));
        }
        if !sqlx::query_scalar::<_, bool>(query)
            .bind(&code)
            .fetch_one(&mut *db)
            .await?
        {
            return Ok(code);
        }
    }
    Err(sqlx::Error::Protocol(
        "Could not generate a unique code".into(),
    ))
}

pub async fn coin(db: &mut MySqlConnection, team: bool) -> Result<i32, sqlx::Error> {
    let query = if team {
        "SELECT EXISTS(SELECT 1 FROM tab_team WHERE tiebreaker = ?)"
    } else {
        "SELECT EXISTS(SELECT 1 FROM tab_debater WHERE tiebreaker = ?)"
    };
    let random = SystemRandom::new();
    for _ in 0..64 {
        let candidate = draw(&random, 65_535)? as i32 + 1;
        if !sqlx::query_scalar::<_, bool>(query)
            .bind(candidate)
            .fetch_one(&mut *db)
            .await?
        {
            return Ok(candidate);
        }
    }
    Err(sqlx::Error::Protocol(
        "Could not assign a unique tiebreaker".into(),
    ))
}
