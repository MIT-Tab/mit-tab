use std::str::SplitWhitespace;

use mittab_tabulation::matching::{maximum_matching, Edge};

use super::{joined, read};

pub fn run(fields: &mut SplitWhitespace<'_>) -> String {
    let size = read(fields);
    let max_cardinality = read::<u8>(fields) == 1;
    let mut edges = Vec::new();
    while let Some(first) = fields.next() {
        edges.push(Edge {
            first: first.parse().unwrap(),
            second: read(fields),
            weight: read(fields),
        });
    }
    let matching = maximum_matching(size, &edges, max_cardinality).unwrap();
    joined(
        matching
            .into_iter()
            .map(|vertex| vertex.map_or(-1, |index| index as isize)),
    )
}
