//! Hash maps keyed by object addresses, which SipHash would only slow down.

use std::collections::HashMap;
use std::hash::{BuildHasherDefault, Hasher};

/// Multiplies by a 64-bit odd constant and rotates the well-mixed high bits
/// down, as rustc-hash 2 (<https://github.com/rust-lang/rustc-hash>) does,
/// since the map indexes buckets by the low bits and addresses are aligned.
#[derive(Default)]
pub struct AddressHasher(u64);

impl Hasher for AddressHasher {
    fn finish(&self) -> u64 {
        self.0.rotate_left(26)
    }

    fn write(&mut self, bytes: &[u8]) {
        for &byte in bytes {
            self.write_u64(u64::from(byte));
        }
    }

    fn write_u64(&mut self, n: u64) {
        self.0 = (self.0.rotate_left(5) ^ n).wrapping_mul(0xf135_7aea_2e62_a9c5);
    }

    fn write_usize(&mut self, n: usize) {
        self.write_u64(n as u64);
    }
}

pub type AddressMap<V> = HashMap<usize, V, BuildHasherDefault<AddressHasher>>;
