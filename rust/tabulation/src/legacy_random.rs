// Integer seeding, word generation and bounded draws compatible with CPython
// 3.10's Random. See LICENSE-MT19937 and CPython Modules/_randommodule.c.
const STATE_SIZE: usize = 624;
const OFFSET: usize = 397;

/// Reproduces existing tournament draws. Credentials use a separate OS CSPRNG.
pub struct LegacyRandom {
    state: [u32; STATE_SIZE],
    index: usize,
}

impl LegacyRandom {
    pub fn seeded(seed: u64) -> Self {
        let words = [seed as u32, (seed >> 32) as u32];
        let key_length = if words[1] == 0 { 1 } else { 2 };
        let mut state = [0_u32; STATE_SIZE];
        state[0] = 19_650_218;
        for index in 1..STATE_SIZE {
            state[index] = 1_812_433_253_u32
                .wrapping_mul(mix(state[index - 1]))
                .wrapping_add(index as u32);
        }

        let mut index = 1;
        for step in 0..STATE_SIZE {
            let key = step % key_length;
            state[index] = (state[index] ^ mix(state[index - 1]).wrapping_mul(1_664_525))
                .wrapping_add(words[key])
                .wrapping_add(key as u32);
            Self::advance(&mut state, &mut index);
        }
        for _ in 0..STATE_SIZE - 1 {
            state[index] = (state[index] ^ mix(state[index - 1]).wrapping_mul(1_566_083_941))
                .wrapping_sub(index as u32);
            Self::advance(&mut state, &mut index);
        }
        state[0] = 0x8000_0000;
        Self {
            state,
            index: STATE_SIZE,
        }
    }

    fn advance(state: &mut [u32; STATE_SIZE], index: &mut usize) {
        *index += 1;
        if *index == STATE_SIZE {
            state[0] = state[STATE_SIZE - 1];
            *index = 1;
        }
    }

    fn word(&mut self) -> u32 {
        if self.index == STATE_SIZE {
            for index in 0..STATE_SIZE {
                let next = self.state[(index + 1) % STATE_SIZE];
                let combined = (self.state[index] & 0x8000_0000) | (next & 0x7fff_ffff);
                let matrix = if combined & 1 == 0 { 0 } else { 0x9908_b0df };
                self.state[index] =
                    self.state[(index + OFFSET) % STATE_SIZE] ^ (combined >> 1) ^ matrix;
            }
            self.index = 0;
        }
        let mut word = self.state[self.index];
        self.index += 1;
        word ^= word >> 11;
        word ^= (word << 7) & 0x9d2c_5680;
        word ^= (word << 15) & 0xefc6_0000;
        word ^ (word >> 18)
    }

    fn bits(&mut self, bits: u32) -> u64 {
        match bits {
            1..=32 => u64::from(self.word() >> (32 - bits)),
            33..=64 => u64::from(self.word()) | (u64::from(self.word() >> (64 - bits)) << 32),
            _ => 0,
        }
    }

    pub fn below(&mut self, maximum: usize) -> usize {
        if maximum == 0 {
            return 0;
        }
        let bits = usize::BITS - maximum.leading_zeros();
        loop {
            let candidate = self.bits(bits) as usize;
            if candidate < maximum {
                return candidate;
            }
        }
    }

    pub fn shuffle<T>(&mut self, values: &mut [T]) {
        for index in (1..values.len()).rev() {
            values.swap(index, self.below(index + 1));
        }
    }
}

fn mix(word: u32) -> u32 {
    word ^ (word >> 30)
}
