// Weighted blossom algorithm ported from mittab/libs/mwmatching.py by Joris
// van Rantwijk. See Galil, "Efficient Algorithms for Finding Maximum Matching
// in Graphs," ACM Computing Surveys, 1986.
use std::collections::HashSet;

#[derive(Clone, Copy, Debug)]
pub struct Edge {
    pub first: usize,
    pub second: usize,
    pub weight: f64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MatchError {
    InvalidEndpoints,
    DuplicateEdge,
    InvalidWeight,
    TooManyVertices,
}

impl Edge {
    fn vertices(self) -> (usize, usize) {
        (self.first, self.second)
    }
}

/// Finds a maximum-weight matching in an undirected graph. When requested,
/// maximum cardinality takes precedence over weight. Edge order breaks ties.
pub fn maximum_matching(
    vertex_count: usize,
    edges: &[Edge],
    max_cardinality: bool,
) -> Result<Vec<Option<usize>>, MatchError> {
    let slots = vertex_count
        .checked_mul(2)
        .ok_or(MatchError::TooManyVertices)?;
    if slots > isize::MAX as usize / size_of::<Blossom>() {
        return Err(MatchError::TooManyVertices);
    }

    let mut seen = HashSet::with_capacity(edges.len());
    let weight_limit = f64::MAX / 4.0 / (vertex_count.max(1) as f64);
    for edge in edges {
        let (first, second) = edge.vertices();
        if first == second || first >= vertex_count || second >= vertex_count {
            return Err(MatchError::InvalidEndpoints);
        }
        if !edge.weight.is_finite() || edge.weight.abs() > weight_limit {
            return Err(MatchError::InvalidWeight);
        }
        if !seen.insert((first.min(second), first.max(second))) {
            return Err(MatchError::DuplicateEdge);
        }
    }
    if edges.is_empty() {
        return Ok(vec![None; vertex_count]);
    }
    Ok(Matcher::new(vertex_count, edges).solve(max_cardinality))
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
enum Label {
    #[default]
    Free,
    Even,
    Odd,
    Visited,
}

// A vertex also has a trivial blossom slot. A nontrivial blossom has an odd
// cycle of children, whose first member contains its base vertex.
#[derive(Clone, Default)]
struct Blossom {
    label: Label,
    incoming: Option<usize>,
    parent: Option<usize>,
    base: Option<usize>,
    children: Vec<usize>,
    connecting_ends: Vec<usize>,
    best_edge: Option<usize>,
    best_neighbor_edges: Option<Vec<usize>>,
    dual: f64,
}

struct Matcher<'a> {
    edges: &'a [Edge],
    // Edge k connects its local endpoints 2k and 2k+1. XOR 1 goes to the
    // opposite endpoint, which is attached to the other vertex.
    endpoints: Vec<usize>,
    neighbors: Vec<Vec<usize>>,
    mate: Vec<Option<usize>>,
    containing: Vec<usize>,
    blossoms: Vec<Blossom>,
    unused_blossoms: Vec<usize>,
    allowable: Vec<bool>,
    queue: Vec<usize>,
}

#[derive(Clone, Copy)]
enum DeltaAction {
    Stop,
    AddEdge { edge: usize, from: usize },
    Expand(usize),
}

impl<'a> Matcher<'a> {
    fn new(count: usize, edges: &'a [Edge]) -> Self {
        let mut endpoints = Vec::new();
        let mut neighbors = vec![Vec::new(); count];
        let mut max_weight = 0.0_f64;
        for (index, edge) in edges.iter().enumerate() {
            let (first, second) = edge.vertices();
            endpoints.extend([first, second]);
            neighbors[first].push(2 * index + 1);
            neighbors[second].push(2 * index);
            max_weight = max_weight.max(edge.weight);
        }

        let mut blossoms = vec![Blossom::default(); 2 * count];
        for (vertex, blossom) in blossoms[..count].iter_mut().enumerate() {
            blossom.base = Some(vertex);
            blossom.dual = max_weight;
        }
        Self {
            edges,
            endpoints,
            neighbors,
            mate: vec![None; count],
            containing: (0..count).collect(),
            blossoms,
            unused_blossoms: (count..2 * count).collect(),
            allowable: vec![false; edges.len()],
            queue: Vec::new(),
        }
    }

    fn vertex_count(&self) -> usize {
        self.mate.len()
    }

    fn slack(&self, edge: usize) -> f64 {
        let Edge {
            first,
            second,
            weight,
        } = self.edges[edge];
        self.blossoms[first].dual + self.blossoms[second].dual - 2.0 * weight
    }

    fn append_leaves(&self, blossom: usize, leaves: &mut Vec<usize>) {
        if blossom < self.vertex_count() {
            leaves.push(blossom);
        } else {
            for &child in &self.blossoms[blossom].children {
                self.append_leaves(child, leaves);
            }
        }
    }

    fn leaves(&self, blossom: usize) -> Vec<usize> {
        let mut leaves = Vec::new();
        self.append_leaves(blossom, &mut leaves);
        leaves
    }

    fn assign_label(&mut self, vertex: usize, label: Label, incoming: Option<usize>) {
        let blossom = self.containing[vertex];
        debug_assert_eq!(self.blossoms[vertex].label, Label::Free);
        debug_assert_eq!(self.blossoms[blossom].label, Label::Free);
        for slot in [vertex, blossom] {
            self.blossoms[slot].label = label;
            self.blossoms[slot].incoming = incoming;
            self.blossoms[slot].best_edge = None;
        }
        match label {
            Label::Even => self.queue.extend(self.leaves(blossom)),
            Label::Odd => {
                let mate = self.mate[self.blossoms[blossom].base.unwrap()].unwrap();
                self.assign_label(self.endpoints[mate], Label::Even, Some(mate ^ 1));
            }
            _ => unreachable!(),
        }
    }

    // Search both tree paths. If they meet, the first common blossom is the
    // base of a new odd cycle. Otherwise the edge joins two free roots.
    fn scan_blossom(&mut self, first: usize, second: usize) -> Option<usize> {
        let mut path = Vec::new();
        let mut result = None;
        let (mut first, mut second) = (Some(first), Some(second));
        while let Some(vertex) = first {
            let blossom = self.containing[vertex];
            if self.blossoms[blossom].label == Label::Visited {
                result = self.blossoms[blossom].base;
                break;
            }
            debug_assert_eq!(self.blossoms[blossom].label, Label::Even);
            self.blossoms[blossom].label = Label::Visited;
            path.push(blossom);
            first = self.blossoms[blossom].incoming.map(|end| {
                let odd_blossom = self.containing[self.endpoints[end]];
                debug_assert_eq!(self.blossoms[odd_blossom].label, Label::Odd);
                self.endpoints[self.blossoms[odd_blossom].incoming.unwrap()]
            });
            if second.is_some() {
                std::mem::swap(&mut first, &mut second);
            }
        }
        for blossom in path {
            self.blossoms[blossom].label = Label::Even;
        }
        result
    }

    fn add_blossom(&mut self, base: usize, edge: usize) {
        let (first, second) = self.edges[edge].vertices();
        let base_child = self.containing[base];
        let blossom = self.unused_blossoms.pop().unwrap();
        let mut children = Vec::new();
        let mut connecting_ends = Vec::new();
        let mut child = self.containing[first];
        while child != base_child {
            let end = self.blossoms[child].incoming.unwrap();
            self.blossoms[child].parent = Some(blossom);
            children.push(child);
            connecting_ends.push(end);
            child = self.containing[self.endpoints[end]];
        }
        self.blossoms[base_child].parent = Some(blossom);
        children.push(base_child);
        children.reverse();
        connecting_ends.reverse();
        connecting_ends.push(2 * edge);
        child = self.containing[second];
        while child != base_child {
            let end = self.blossoms[child].incoming.unwrap();
            self.blossoms[child].parent = Some(blossom);
            children.push(child);
            connecting_ends.push(end ^ 1);
            child = self.containing[self.endpoints[end]];
        }

        self.blossoms[blossom] = Blossom {
            label: Label::Even,
            incoming: self.blossoms[base_child].incoming,
            base: Some(base),
            children,
            connecting_ends,
            ..Blossom::default()
        };
        for vertex in self.leaves(blossom) {
            if self.blossoms[self.containing[vertex]].label == Label::Odd {
                self.queue.push(vertex);
            }
            self.containing[vertex] = blossom;
        }
        self.find_neighbor_edges(blossom);
    }

    fn find_neighbor_edges(&mut self, blossom: usize) {
        let mut best_to = vec![None; self.blossoms.len()];
        for child in self.blossoms[blossom].children.clone() {
            let edges = self.blossoms[child]
                .best_neighbor_edges
                .take()
                .unwrap_or_else(|| {
                    self.leaves(child)
                        .iter()
                        .flat_map(|&vertex| self.neighbors[vertex].iter().map(|&end| end / 2))
                        .collect()
                });
            for edge in edges {
                let (first, second) = self.edges[edge].vertices();
                let mut outside = self.containing[second];
                if outside == blossom {
                    outside = self.containing[first];
                }
                if outside != blossom
                    && self.blossoms[outside].label == Label::Even
                    && best_to[outside].is_none_or(|best| self.slack(edge) < self.slack(best))
                {
                    best_to[outside] = Some(edge);
                }
            }
            self.blossoms[child].best_edge = None;
        }
        let edges: Vec<_> = best_to.into_iter().flatten().collect();
        self.blossoms[blossom].best_edge = edges.iter().copied().reduce(|best, edge| {
            if self.slack(edge) < self.slack(best) {
                edge
            } else {
                best
            }
        });
        self.blossoms[blossom].best_neighbor_edges = Some(edges);
    }

    fn expand_blossom(&mut self, blossom: usize, end_stage: bool) {
        let children = std::mem::take(&mut self.blossoms[blossom].children);
        let connecting_ends = std::mem::take(&mut self.blossoms[blossom].connecting_ends);
        for &child in &children {
            self.blossoms[child].parent = None;
            if child >= self.vertex_count() && end_stage && self.blossoms[child].dual == 0.0 {
                self.expand_blossom(child, end_stage);
            } else {
                for leaf in self.leaves(child) {
                    self.containing[leaf] = child;
                }
            }
        }
        if !end_stage && self.blossoms[blossom].label == Label::Odd {
            self.relabel_expanded_blossom(
                &children,
                &connecting_ends,
                self.blossoms[blossom].incoming.unwrap(),
            );
        }
        self.blossoms[blossom] = Blossom::default();
        self.unused_blossoms.push(blossom);
    }

    fn relabel_expanded_blossom(
        &mut self,
        children: &[usize],
        connecting_ends: &[usize],
        mut end: usize,
    ) {
        let entry = self.containing[self.endpoints[end ^ 1]];
        let index = children.iter().position(|&child| child == entry).unwrap();
        let mut path = CyclePath::new(index, children.len());
        while !path.at_base() {
            self.blossoms[self.endpoints[end ^ 1]].label = Label::Free;
            let next_end = path.connection(connecting_ends);
            self.blossoms[self.endpoints[next_end ^ 1]].label = Label::Free;
            self.assign_label(self.endpoints[end ^ 1], Label::Odd, Some(end));
            self.allowable[next_end / 2] = true;
            path.step();
            end = path.connection(connecting_ends);
            self.allowable[end / 2] = true;
            path.step();
        }

        // The base child keeps its mate in the alternating tree.
        let base_child = children[path.index()];
        for slot in [self.endpoints[end ^ 1], base_child] {
            self.blossoms[slot].label = Label::Odd;
            self.blossoms[slot].incoming = Some(end);
        }
        self.blossoms[base_child].best_edge = None;
        path.step();
        while children[path.index()] != entry {
            let child = children[path.index()];
            if self.blossoms[child].label != Label::Even {
                if let Some(vertex) = self
                    .leaves(child)
                    .into_iter()
                    .find(|&vertex| self.blossoms[vertex].label != Label::Free)
                {
                    debug_assert_eq!(self.blossoms[vertex].label, Label::Odd);
                    self.blossoms[vertex].label = Label::Free;
                    let base_mate = self.mate[self.blossoms[child].base.unwrap()].unwrap();
                    self.blossoms[self.endpoints[base_mate]].label = Label::Free;
                    self.assign_label(vertex, Label::Odd, self.blossoms[vertex].incoming);
                }
            }
            path.step();
        }
    }

    fn augment_blossom(&mut self, blossom: usize, vertex: usize) {
        let mut child = vertex;
        while self.blossoms[child].parent != Some(blossom) {
            child = self.blossoms[child].parent.unwrap();
        }
        if child >= self.vertex_count() {
            self.augment_blossom(child, vertex);
        }
        let index = self.blossoms[blossom]
            .children
            .iter()
            .position(|&entry| entry == child)
            .unwrap();
        let mut path = CyclePath::new(index, self.blossoms[blossom].children.len());
        while !path.at_base() {
            path.step();
            let end = path.connection(&self.blossoms[blossom].connecting_ends);
            for connection in [end, end ^ 1] {
                let child = self.blossoms[blossom].children[path.index()];
                if child >= self.vertex_count() {
                    self.augment_blossom(child, self.endpoints[connection]);
                }
                self.mate[self.endpoints[connection]] = Some(connection ^ 1);
                if connection == end {
                    path.step();
                }
            }
        }
        self.blossoms[blossom].children.rotate_left(index);
        self.blossoms[blossom].connecting_ends.rotate_left(index);
        self.blossoms[blossom].base = self.blossoms[self.blossoms[blossom].children[0]].base;
        debug_assert_eq!(self.blossoms[blossom].base, Some(vertex));
    }

    fn augment_matching(&mut self, edge: usize) {
        let (first, second) = self.edges[edge].vertices();
        for (mut vertex, mut end) in [(first, edge * 2 + 1), (second, edge * 2)] {
            loop {
                let even = self.containing[vertex];
                debug_assert_eq!(self.blossoms[even].label, Label::Even);
                if even >= self.vertex_count() {
                    self.augment_blossom(even, vertex);
                }
                self.mate[vertex] = Some(end);
                let Some(incoming) = self.blossoms[even].incoming else {
                    break;
                };
                let odd = self.containing[self.endpoints[incoming]];
                debug_assert_eq!(self.blossoms[odd].label, Label::Odd);
                let odd_end = self.blossoms[odd].incoming.unwrap();
                vertex = self.endpoints[odd_end];
                let entry = self.endpoints[odd_end ^ 1];
                if odd >= self.vertex_count() {
                    self.augment_blossom(odd, entry);
                }
                self.mate[entry] = Some(odd_end);
                end = odd_end ^ 1;
            }
        }
    }

    fn start_stage(&mut self) {
        let count = self.vertex_count();
        self.queue.clear();
        self.allowable.fill(false);
        for blossom in &mut self.blossoms {
            blossom.label = Label::Free;
            blossom.best_edge = None;
            blossom.best_neighbor_edges = None;
        }
        for vertex in 0..count {
            if self.mate[vertex].is_none()
                && self.blossoms[self.containing[vertex]].label == Label::Free
            {
                self.assign_label(vertex, Label::Even, None);
            }
        }
    }

    fn find_augmenting_path(&mut self) -> bool {
        while let Some(vertex) = self.queue.pop() {
            debug_assert_eq!(self.blossoms[self.containing[vertex]].label, Label::Even);
            for index in 0..self.neighbors[vertex].len() {
                let end = self.neighbors[vertex][index];
                let next = self.endpoints[end];
                let edge = end / 2;
                let current_blossom = self.containing[vertex];
                let next_blossom = self.containing[next];
                if current_blossom == next_blossom {
                    continue;
                }

                if !self.allowable[edge] && self.slack(edge) <= 0.0 {
                    self.allowable[edge] = true;
                }
                let next_label = self.blossoms[next_blossom].label;
                if self.allowable[edge] {
                    match next_label {
                        Label::Free => self.assign_label(next, Label::Odd, Some(end ^ 1)),
                        Label::Even => {
                            if let Some(base) = self.scan_blossom(vertex, next) {
                                self.add_blossom(base, edge);
                            } else {
                                self.augment_matching(edge);
                                return true;
                            }
                        }
                        Label::Odd if self.blossoms[next].label == Label::Free => {
                            self.blossoms[next].label = Label::Odd;
                            self.blossoms[next].incoming = Some(end ^ 1);
                        }
                        _ => {}
                    }
                } else {
                    let slot = if next_label == Label::Even {
                        current_blossom
                    } else if self.blossoms[next].label == Label::Free {
                        next
                    } else {
                        continue;
                    };
                    if self.blossoms[slot]
                        .best_edge
                        .is_none_or(|best| self.slack(edge) < self.slack(best))
                    {
                        self.blossoms[slot].best_edge = Some(edge);
                    }
                }
            }
        }
        false
    }

    fn next_delta(&self, max_cardinality: bool) -> (f64, DeltaAction) {
        use DeltaAction::*;
        let count = self.vertex_count();
        let minimum_vertex_dual = || {
            self.blossoms[..count]
                .iter()
                .map(|blossom| blossom.dual)
                .fold(f64::INFINITY, f64::min)
        };
        let mut delta = if max_cardinality {
            f64::INFINITY
        } else {
            minimum_vertex_dual()
        };
        let mut action = Stop;

        // The comparisons must stay in this order for equal-weight matchings.
        for vertex in 0..count {
            if self.blossoms[self.containing[vertex]].label == Label::Free {
                if let Some(edge) = self.blossoms[vertex].best_edge {
                    let slack = self.slack(edge);
                    if slack < delta {
                        delta = slack;
                        let (first, second) = self.edges[edge].vertices();
                        let from = if self.blossoms[self.containing[first]].label == Label::Free {
                            second
                        } else {
                            first
                        };
                        action = AddEdge { edge, from };
                    }
                }
            }
        }
        for blossom in &self.blossoms {
            if blossom.parent.is_none() && blossom.label == Label::Even {
                if let Some(edge) = blossom.best_edge {
                    let half_slack = self.slack(edge) / 2.0;
                    if half_slack < delta {
                        delta = half_slack;
                        action = AddEdge {
                            edge,
                            from: self.edges[edge].first,
                        };
                    }
                }
            }
        }
        for (index, blossom) in self.blossoms.iter().enumerate().skip(count) {
            if blossom.base.is_some()
                && blossom.parent.is_none()
                && blossom.label == Label::Odd
                && blossom.dual < delta
            {
                delta = blossom.dual;
                action = Expand(index);
            }
        }
        if delta == f64::INFINITY {
            delta = minimum_vertex_dual().max(0.0);
        }
        (delta, action)
    }

    fn update_duals(&mut self, delta: f64) {
        let count = self.vertex_count();
        for vertex in 0..count {
            match self.blossoms[self.containing[vertex]].label {
                Label::Even => self.blossoms[vertex].dual -= delta,
                Label::Odd => self.blossoms[vertex].dual += delta,
                _ => {}
            }
        }
        for blossom in &mut self.blossoms[count..] {
            if blossom.base.is_some() && blossom.parent.is_none() {
                match blossom.label {
                    Label::Even => blossom.dual += delta,
                    Label::Odd => blossom.dual -= delta,
                    _ => {}
                }
            }
        }
    }

    fn solve(mut self, max_cardinality: bool) -> Vec<Option<usize>> {
        let count = self.vertex_count();
        'stages: for _ in 0..count {
            self.start_stage();
            while !self.find_augmenting_path() {
                let (delta, action) = self.next_delta(max_cardinality);
                self.update_duals(delta);
                match action {
                    DeltaAction::Stop => break 'stages,
                    DeltaAction::AddEdge { edge, from } => {
                        self.allowable[edge] = true;
                        self.queue.push(from);
                    }
                    DeltaAction::Expand(blossom) => self.expand_blossom(blossom, false),
                }
            }

            for index in count..self.blossoms.len() {
                let blossom = &self.blossoms[index];
                if blossom.base.is_some()
                    && blossom.parent.is_none()
                    && blossom.label == Label::Even
                    && blossom.dual == 0.0
                {
                    self.expand_blossom(index, true);
                }
            }
        }
        self.mate
            .into_iter()
            .map(|end| end.map(|end| self.endpoints[end]))
            .collect()
    }
}

struct CyclePath {
    position: isize,
    direction: isize,
    reverse_endpoint: usize,
    size: isize,
}

impl CyclePath {
    fn new(index: usize, size: usize) -> Self {
        let (position, direction, reverse_endpoint) = if index % 2 == 1 {
            (index as isize - size as isize, 1, 0)
        } else {
            (index as isize, -1, 1)
        };
        Self {
            position,
            direction,
            reverse_endpoint,
            size: size as isize,
        }
    }

    fn step(&mut self) {
        self.position += self.direction;
    }

    fn at_base(&self) -> bool {
        self.position == 0
    }

    fn index(&self) -> usize {
        self.position.rem_euclid(self.size) as usize
    }

    fn connection(&self, ends: &[usize]) -> usize {
        let slot = (self.position - self.reverse_endpoint as isize).rem_euclid(self.size) as usize;
        ends[slot] ^ self.reverse_endpoint
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn optimum(graph: &[Vec<Option<i16>>], remaining: u16, cardinality: bool) -> (usize, i16) {
        if remaining == 0 {
            return (0, 0);
        }
        let first = remaining.trailing_zeros() as usize;
        let rest = remaining & !(1 << first);
        let mut best = optimum(graph, rest, cardinality);
        for second in first + 1..graph.len() {
            if rest & (1 << second) == 0 {
                continue;
            }
            if let Some(weight) = graph[first][second] {
                let (pairs, score) = optimum(graph, rest & !(1 << second), cardinality);
                let candidate = (pairs + 1, score + weight);
                if (cardinality && candidate > best) || (!cardinality && candidate.1 > best.1) {
                    best = candidate;
                }
            }
        }
        best
    }

    #[test]
    fn matches_brute_force_on_all_five_vertex_topologies() {
        const SIZE: usize = 5;
        let pairs: Vec<_> = (0..SIZE)
            .flat_map(|first| (first + 1..SIZE).map(move |second| (first, second)))
            .collect();
        for topology in 0..(1 << pairs.len()) {
            let mut graph = vec![vec![None; SIZE]; SIZE];
            let mut edges = Vec::new();
            for (index, &(first, second)) in pairs.iter().enumerate() {
                if topology & (1 << index) != 0 {
                    let weight = ((topology * 3 + index * 7) % 9) as i16 - 4;
                    graph[first][second] = Some(weight);
                    edges.push(Edge {
                        first,
                        second,
                        weight: f64::from(weight),
                    });
                }
            }
            for max_cardinality in [false, true] {
                let expected = optimum(&graph, (1 << SIZE) - 1, max_cardinality);
                let matching = maximum_matching(SIZE, &edges, max_cardinality).unwrap();
                let mut actual = (0, 0);
                for (first, second) in matching.iter().enumerate() {
                    if let Some(second) = *second {
                        assert_eq!(matching[second], Some(first));
                        if first < second {
                            actual.0 += 1;
                            actual.1 += graph[first][second].unwrap();
                        }
                    }
                }
                if max_cardinality {
                    assert_eq!(actual, expected, "{edges:?}");
                } else {
                    assert_eq!(actual.1, expected.1, "{edges:?}");
                }
            }
        }
    }

    #[test]
    fn rejects_malformed_graphs() {
        let edge = |first, second, weight| Edge {
            first,
            second,
            weight,
        };
        assert_eq!(
            maximum_matching(2, &[edge(0, 0, 1.0)], true),
            Err(MatchError::InvalidEndpoints)
        );
        assert_eq!(
            maximum_matching(2, &[edge(0, 2, 1.0)], true),
            Err(MatchError::InvalidEndpoints)
        );
        assert_eq!(
            maximum_matching(3, &[edge(0, 1, 1.0), edge(1, 0, 2.0)], true),
            Err(MatchError::DuplicateEdge)
        );
        assert_eq!(
            maximum_matching(2, &[edge(0, 1, f64::NAN)], true),
            Err(MatchError::InvalidWeight)
        );
    }
}
