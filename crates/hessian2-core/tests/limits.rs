//! Limits wire contracts.

use hessian2_core::{ErrorKind, MAX_SKIP_DEPTH, Reader};

#[test]
fn skip_accepts_a_value_exactly_at_the_depth_limit() {
    // MAX_SKIP_DEPTH includes the scalar leaf in Reader::skip's public bound.
    let mut bytes = vec![0x79; MAX_SKIP_DEPTH - 1];
    bytes.push(b'N');
    let mut reader = Reader::new(&bytes);

    reader.skip().unwrap();

    assert!(reader.is_empty());
}

#[test]
fn skip_rejects_one_value_past_the_depth_limit() {
    let mut bytes = vec![0x79; MAX_SKIP_DEPTH];
    bytes.push(b'N');
    let error = Reader::new(&bytes).skip().unwrap_err();

    assert_eq!(
        (error.kind(), error.offset()),
        (ErrorKind::DepthLimit, MAX_SKIP_DEPTH)
    );
    assert_eq!(
        error.to_string(),
        format!("values nest too deeply at byte {MAX_SKIP_DEPTH}")
    );
}
