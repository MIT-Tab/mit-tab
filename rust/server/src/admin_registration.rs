use axum::{
    http::{HeaderMap, StatusCode},
    response::Response,
};
use chrono::NaiveDateTime;
use minijinja::{Value, context};
use sqlx::{MySql, QueryBuilder};

use crate::{
    App, access,
    admin_changelist::{Page, Query, default_list, filter_ids},
    admin_html::{self, Shell, django_datetime},
    admin_records::{Column, headings, order_sql, search_terms},
    legacy_html::escape,
    legacy_session::Actor,
    private, unavailable,
};

const COLUMNS: &[Column] = &[
    Column::new("school", "School", "s.name", true),
    Column::new("email", "Email", "t.email", false),
    Column::new(
        "herokunator_code",
        "Herokunator code",
        "t.herokunator_code",
        false,
    ),
    Column::new("created_at", "Created at", "t.created_at", true),
];

pub(super) fn search(sql: &mut QueryBuilder<MySql>, query: &Query) {
    for term in search_terms(query.get("q").unwrap_or_default()) {
        let pattern = format!(
            "%{}%",
            term.replace('!', "!!")
                .replace('%', "!%")
                .replace('_', "!_")
        );
        sql.push(" AND (s.name LIKE ")
            .push_bind(pattern.clone())
            .push(" ESCAPE '!' OR t.email LIKE ")
            .push_bind(pattern.clone())
            .push(" ESCAPE '!' OR t.herokunator_code LIKE ")
            .push_bind(pattern)
            .push(" ESCAPE '!')");
    }
}

pub async fn list(app: &App, actor: Actor, headers: &HeaderMap, query: Query) -> Response {
    let config = default_list("registration").expect("registration changelist");
    let model = config.model;
    let path = model.url();
    let shell = match Shell::load(app, actor, headers, &path).await {
        Ok(shell) => shell,
        Err(response) => return *response,
    };
    if query
        .get("_to_field")
        .is_some_and(|name| !matches!(name, "" | "id"))
    {
        return private((StatusCode::BAD_REQUEST, "Invalid related object field."));
    }
    let Some(ids) = query.record_ids(&[]) else {
        return access::found(format!("{path}?e=1"));
    };
    let popup = query.get("_popup").is_some();
    let offset = usize::from(!popup);
    let order: Vec<_> = query
        .get("o")
        .unwrap_or_default()
        .split('.')
        .filter_map(|part| {
            let index = part
                .trim_start_matches('-')
                .parse::<usize>()
                .ok()?
                .checked_sub(offset)?;
            (index < COLUMNS.len()).then_some((index, !part.starts_with('-')))
        })
        .collect();
    let result = async {
        let mut db = app.db.begin().await?;
        let full: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM registration_registration")
            .fetch_one(&mut *db).await?;
        let from = format!(" FROM {} WHERE TRUE", config.source);
        let mut total = QueryBuilder::new(format!("SELECT COUNT(*){from}"));
        filter_ids(&mut total, &ids);
        search(&mut total, &query);
        let count = total.build_query_scalar::<i64>().fetch_one(&mut *db).await?;
        let Some(page) = Page::new(&query, count) else {
            return Ok(access::found(format!("{path}?e=1")));
        };
        let (limit, start) = page.window();
        let mut selection = QueryBuilder::new(format!(
            "SELECT t.id, s.name, t.email, t.herokunator_code, t.created_at{from}"
        ));
        filter_ids(&mut selection, &ids);
        search(&mut selection, &query);
        selection.push(" ORDER BY ").push(order_sql(COLUMNS, &order))
            .push(" LIMIT ").push_bind(limit).push(" OFFSET ").push_bind(start);
        let rows = selection.build_query_as::<(i32, String, String, String, NaiveDateTime)>()
            .fetch_all(&mut *db).await?;
        db.commit().await?;
        let suffix = escape(&query.suffix());
        let results: Vec<_> = rows.iter().map(|(id, school, email, code, created)| {
            let opener = if popup { format!(r#" data-popup-opener="{id}""#) } else { String::new() };
            let mut cells = Vec::new();
            if !popup {
                cells.push(format!(r#"<td class="action-checkbox"><input type="checkbox" name="_selected_action" value="{id}" class="action-select"></td>"#));
            }
            cells.extend([
                format!(r#"<th class="field-school nowrap"><a href="{path}{id}/change/{suffix}"{opener}>{}</a></th>"#, escape(school)),
                format!(r#"<td class="field-email">{}</td>"#, escape(email)),
                format!(r#"<td class="field-herokunator_code">{}</td>"#, escape(code)),
                format!(r#"<td class="field-created_at nowrap">{}</td>"#, django_datetime(*created)),
            ]);
            cells
        }).collect();
        let Ok(search_form) = admin_html::render_fragment("admin/search_form.html", context! {
            cl => context! {
                search_fields => true,
                query => query.get("q").unwrap_or_default().trim(),
                result_count => count, full_result_count => full,
                is_popup => popup, show_full_result_count => true, params => query.ordered_params(),
            },
            search_var => "q", is_popup_var => "_popup",
            show_result_count => count != full,
        }) else { return Ok(unavailable()); };
        let opts = model.opts();
        let pager = page.context(&query);
        let mut result_headers: Vec<Value> = Vec::new();
        if !popup {
            result_headers.push(context! {
                class_attrib => r#" class="action-checkbox-column""#,
                text => r#"<input type="checkbox" id="action-toggle">"#,
            });
        }
        result_headers.extend(headings(COLUMNS, &order, &query, offset));
        let action_widget = r#"<select name="action" required>
  <option value="" selected>---------</option>

  <option value="delete_selected">Delete selected registrations</option>

</select>"#;
        let title = if popup { "Select registration" } else { "Select registration to change" };
        Ok::<_, sqlx::Error>(shell.render("admin/change_list.html", context! {
            title, opts => &opts, is_popup => popup,
            cl => context! {
                opts => &opts, show_admin_actions => full > 0,
                result_count => count, result_list => &rows,
            },
            app_url => "/admin/registration/", model_url => &path,
            filter_suffix => query.add_suffix(),
            module_name => model.plural, has_add_permission => true,
            actions_on_top => true, action_form => !popup,
            action_fields => [
                context! { label => "Action:", widget => action_widget },
                context! { widget => r#"<input type="hidden" name="select_across" value="0" class="select-across">"# },
            ],
            actions_selection_counter => true,
            selection_note => format!("0 of {} selected", rows.len()),
            selection_note_all => format!("All {count} selected"),
            media => context! { js => admin_html::scripts() },
            search_form, result_headers, num_sorted_fields => order.len(), results,
            ..pager,
        }, headers))
    }.await;
    result.unwrap_or_else(|_| unavailable())
}
