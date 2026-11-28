use std::collections::{HashMap, HashSet};

use crate::matching::{maximum_matching, Edge, MatchError};

#[derive(Clone, Copy, Debug)]
pub struct Tag {
    pub id: i64,
    pub priority_hundredths: i32,
}

#[derive(Debug)]
pub struct Room<'a> {
    pub rank_hundredths: i32,
    pub tags: &'a [i64],
}

#[derive(Debug, PartialEq, Eq)]
pub enum RoomError {
    InsufficientRooms { rounds: usize, rooms: usize },
    NoAssignment(usize),
    Matching(MatchError),
}

/// Assigns one room to each debate in pairing order. Checked-in rooms start in
/// their original order, which remains the tie-break for equal-ranked rooms.
pub fn assign_rooms(
    required: &[&[Tag]],
    rooms: &[Room<'_>],
    seed_rooms: bool,
) -> Result<Vec<usize>, RoomError> {
    if required.len() > rooms.len() {
        return Err(RoomError::InsufficientRooms {
            rounds: required.len(),
            rooms: rooms.len(),
        });
    }
    if required.is_empty() {
        return Ok(Vec::new());
    }
    let mut room_order: Vec<_> = (0..rooms.len()).collect();
    room_order.sort_by_key(|&index| std::cmp::Reverse(rooms[index].rank_hundredths));
    let room_tags: Vec<HashSet<_>> = room_order
        .iter()
        .map(|&index| rooms[index].tags.iter().copied().collect())
        .collect();

    let mut edges = Vec::new();
    for (pairing, tags) in required.iter().enumerate() {
        let priorities: HashMap<_, _> = tags
            .iter()
            .map(|tag| (tag.id, tag.priority_hundredths))
            .collect();
        for (position, tags) in room_tags.iter().enumerate() {
            let room = &rooms[room_order[position]];
            let missing_priority: i64 = priorities
                .iter()
                .filter(|(tag, _)| !tags.contains(tag))
                .map(|(_, &priority)| i64::from(priority))
                .sum();
            // Room rank earns 100 points per unit and missing tag priority
            // costs 1000 points per unit, both exact in integer hundredths.
            let seed_distance = if seed_rooms {
                pairing.abs_diff(position)
            } else {
                0
            };
            let weight =
                i64::from(room.rank_hundredths) - 10 * missing_priority - seed_distance as i64;
            edges.push(Edge {
                first: pairing,
                second: required.len() + position,
                weight: weight as f64,
            });
        }
    }

    let mates = maximum_matching(required.len() + rooms.len(), &edges, true)
        .map_err(RoomError::Matching)?;
    mates[..required.len()]
        .iter()
        .enumerate()
        .map(|(index, position)| {
            position
                .map(|position| room_order[position - required.len()])
                .ok_or(RoomError::NoAssignment(index))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_tags_have_priority_over_rank_and_seeding() {
        let tags = [Tag {
            id: 1,
            priority_hundredths: 1_000,
        }];
        let rooms = [
            Room {
                rank_hundredths: 500,
                tags: &[],
            },
            Room {
                rank_hundredths: 100,
                tags: &[1],
            },
        ];
        assert_eq!(assign_rooms(&[&tags, &[]], &rooms, true).unwrap(), [1, 0]);
        assert_eq!(assign_rooms(&[&tags], &rooms, false).unwrap(), [1]);
    }

    #[test]
    fn insufficient_rooms_report_the_required_and_available_counts() {
        assert_eq!(
            assign_rooms(&[&[]], &[], true),
            Err(RoomError::InsufficientRooms {
                rounds: 1,
                rooms: 0
            })
        );
        assert!(assign_rooms(&[], &[], false).unwrap().is_empty());
    }
}
