use sqlx::{MySqlConnection, QueryBuilder};

#[derive(Clone, Copy)]
pub enum Entity {
    Team,
    Judge,
    Debater,
}

pub async fn unique_name(
    db: &mut MySqlConnection,
    entity: Entity,
    base: &str,
    except: Option<i32>,
) -> Result<String, sqlx::Error> {
    let query = match entity {
        Entity::Team => {
            "SELECT EXISTS(SELECT 1 FROM tab_team WHERE name = ? AND (? IS NULL OR id <> ?))"
        }
        Entity::Judge => {
            "SELECT EXISTS(SELECT 1 FROM tab_judge WHERE name = ? AND (? IS NULL OR id <> ?))"
        }
        Entity::Debater => {
            "SELECT EXISTS(SELECT 1 FROM tab_debater WHERE name = ? AND (? IS NULL OR id <> ?))"
        }
    };
    let maximum = if matches!(entity, Entity::Team) {
        35
    } else {
        30
    };
    for index in 0..100_000 {
        let name = if index == 0 {
            base.to_string()
        } else {
            let suffix = format!(" ({index})");
            let truncated: String = base.chars().take(maximum - suffix.len()).collect();
            format!("{}{suffix}", truncated.trim_end())
        };
        if !sqlx::query_scalar::<_, bool>(query)
            .bind(&name)
            .bind(except)
            .bind(except)
            .fetch_one(&mut *db)
            .await?
        {
            return Ok(name);
        }
    }
    Err(sqlx::Error::Protocol(
        "Could not assign a unique name".into(),
    ))
}

pub async fn delete_team(db: &mut MySqlConnection, id: i32) -> Result<(), sqlx::Error> {
    let rounds = sqlx::query_scalar::<_, i32>(
        "SELECT id FROM tab_round WHERE gov_team_id = ? OR opp_team_id = ?",
    )
    .bind(id)
    .bind(id)
    .fetch_all(&mut *db)
    .await?;
    let outrounds = sqlx::query_scalar::<_, i32>(
        "SELECT id FROM tab_outround WHERE gov_team_id = ? OR opp_team_id = ?",
    )
    .bind(id)
    .bind(id)
    .fetch_all(&mut *db)
    .await?;
    delete_rounds(db, &rounds).await?;
    delete_outrounds(db, &outrounds).await?;
    sqlx::query("DELETE FROM tab_outroundchallenge WHERE challenger_id = ? OR challenged_id = ?")
        .bind(id)
        .bind(id)
        .execute(&mut *db)
        .await?;
    delete_links(
        db,
        &[id],
        &[
            ("tab_breakingteam", "team_id"),
            ("tab_bye", "bye_team_id"),
            ("tab_noshow", "no_show_team_id"),
            ("tab_publishedteamplacement", "team_id"),
            ("tab_rankinggroup_teams", "team_id"),
            ("tab_scratch", "team_id"),
            ("tab_team_debaters", "team_id"),
            ("tab_team_required_room_tags", "team_id"),
            ("tab_teamcodeemaillog", "team_id"),
            ("tab_team", "id"),
        ],
    )
    .await
}

pub async fn delete_judge(db: &mut MySqlConnection, id: i32) -> Result<(), sqlx::Error> {
    let rounds = sqlx::query_scalar::<_, i32>("SELECT id FROM tab_round WHERE chair_id = ?")
        .bind(id)
        .fetch_all(&mut *db)
        .await?;
    let outrounds = sqlx::query_scalar::<_, i32>("SELECT id FROM tab_outround WHERE chair_id = ?")
        .bind(id)
        .fetch_all(&mut *db)
        .await?;
    delete_rounds(db, &rounds).await?;
    delete_outrounds(db, &outrounds).await?;
    delete_links(
        db,
        &[id],
        &[
            ("tab_checkin", "judge_id"),
            ("tab_judge_required_room_tags", "judge_id"),
            ("tab_judge_schools", "judge_id"),
            ("tab_judgecodeemaillog", "judge_id"),
            ("tab_judgeexpectedcheckin", "judge_id"),
            ("tab_manualjudgeassignment", "judge_id"),
            ("tab_outround_judges", "judge_id"),
            ("tab_round_judges", "judge_id"),
            ("tab_scratch", "judge_id"),
            ("tab_judge", "id"),
        ],
    )
    .await
}

pub async fn delete_debater(db: &mut MySqlConnection, id: i32) -> Result<(), sqlx::Error> {
    delete_links(
        db,
        &[id],
        &[
            ("tab_roundstats", "debater_id"),
            ("tab_rankinggroup_debaters", "debater_id"),
            ("tab_debater", "id"),
        ],
    )
    .await
}

pub async fn delete_rounds(db: &mut MySqlConnection, ids: &[i32]) -> Result<(), sqlx::Error> {
    delete_links(
        db,
        ids,
        &[
            ("tab_manualjudgeassignment", "round_id"),
            ("tab_round_judges", "round_id"),
            ("tab_roundstats", "round_id"),
            ("tab_writtenrfdemaillog", "round_id"),
            ("tab_round", "id"),
        ],
    )
    .await
}

pub async fn delete_outrounds(db: &mut MySqlConnection, ids: &[i32]) -> Result<(), sqlx::Error> {
    delete_links(
        db,
        ids,
        &[
            ("tab_outround_judges", "outround_id"),
            ("tab_outround", "id"),
        ],
    )
    .await
}

pub async fn delete_links(
    db: &mut MySqlConnection,
    ids: &[i32],
    links: &[(&str, &str)],
) -> Result<(), sqlx::Error> {
    if ids.is_empty() {
        return Ok(());
    }
    for (table, column) in links {
        let mut query = QueryBuilder::new("DELETE FROM ");
        query.push(table).push(" WHERE ").push(column).push(" IN (");
        let mut fields = query.separated(", ");
        for id in ids {
            fields.push_bind(id);
        }
        query.push(")").build().execute(&mut *db).await?;
    }
    Ok(())
}
