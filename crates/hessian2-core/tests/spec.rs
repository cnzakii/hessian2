//! The examples of the Hessian 2.0 serialization specification
//! (<http://hessian.caucho.com/doc/hessian-serialization.html>), by figure.
//!
//! Examples the specification gets wrong are tested in their corrected form,
//! with the correction noted at each test; scalar examples are also covered
//! by the unit tests in `lib.rs`.

use hessian2_core::{Reader, Token, Writer};

/// Renders every value of a stream in a compact notation.
fn render(bytes: &[u8]) -> String {
    let mut reader = Reader::new(bytes);
    let mut out = Vec::new();
    while !reader.is_empty() {
        out.push(value(&mut reader));
    }
    out.join(" ")
}

fn value(reader: &mut Reader<'_>) -> String {
    match reader.next_token().expect("valid stream") {
        Token::Null => "N".into(),
        Token::Bool(b) => if b { "T" } else { "F" }.into(),
        Token::Int(n) => format!("{n}"),
        Token::Long(n) => format!("{n}L"),
        Token::Double(d) => format!("{d:?}D"),
        Token::Date(ms) => format!("date({ms})"),
        Token::String(s) => format!("{:?}", String::from_utf8_lossy(&s)),
        Token::Binary(b) => format!("bin({})", b.len()),
        Token::List { type_name, len } => {
            let mut items = Vec::new();
            match len {
                Some(len) => (0..len).for_each(|_| items.push(value(reader))),
                None => {
                    while !reader.end().unwrap() {
                        items.push(value(reader));
                    }
                }
            }
            let ty = type_name.map_or(String::new(), |t| format!("{}:", reader.type_name(t)));
            format!("[{ty}{}]", items.join(", "))
        }
        Token::Map { type_name } => {
            let mut pairs = Vec::new();
            while !reader.end().unwrap() {
                let key = value(reader);
                pairs.push(format!("{key}={}", value(reader)));
            }
            let ty = type_name.map_or(String::new(), |t| format!("{}:", reader.type_name(t)));
            format!("{{{ty}{}}}", pairs.join(", "))
        }
        Token::Object { class } => {
            let def = reader.class(class).clone();
            let fields: Vec<_> = def
                .fields
                .iter()
                .map(|f| format!("{f}={}", value(reader)))
                .collect();
            format!("{}({})", def.name, fields.join(", "))
        }
        Token::Ref(n) => format!("@{n}"),
    }
}

fn concat(parts: &[&[u8]]) -> Vec<u8> {
    parts.concat()
}

/// §4.1.2 Figure 3, with the non-final chunk tag the bytecode map gives (`A`).
#[test]
fn figure_3_binary() {
    assert_eq!(render(&[0x20]), "bin(0)");
    assert_eq!(render(&[0x23, 1, 2, 3]), "bin(3)");
    let final_chunk = concat(&[b"B\x10\x00", &[0; 0x1000]]);
    assert_eq!(render(&final_chunk), "bin(4096)");
    let chunked = concat(&[b"A\x04\x00", &[0; 0x400], b"\x23\x01\x02\x03"]);
    assert_eq!(render(&chunked), "bin(1027)");
}

/// §4.2.1 Figure 5.
#[test]
fn figure_5_boolean() {
    assert_eq!(render(b"TF"), "T F");
}

/// §4.4.6 Figure 10.
#[test]
fn figure_10_double() {
    assert_eq!(
        render(b"\x5b\x5c\x5d\x00\x5d\x80\x5d\x7f\x5e\x00\x00\x5e\x80\x00\x5e\x7f\xff"),
        "0.0D 1.0D 0.0D -128.0D 127.0D 0.0D -32768.0D 32767.0D"
    );
    assert_eq!(render(b"D\x40\x28\x80\x00\x00\x00\x00\x00"), "12.25D");
}

/// §4.6.2 Figures 14, 15, and 16.
#[test]
fn figures_14_to_16_lists() {
    assert_eq!(render(b"V\x04[int\x92\x90\x91"), "[[int:0, 1]");
    assert_eq!(render(b"\x57\x90\x91Z"), "[0, 1]");
    assert_eq!(
        render(b"\x72\x04[int\x90\x91\x73\x90\x92\x93\x94"),
        "[[int:0, 1] [[int:2, 3, 4]"
    );
}

/// §4.8.1 Figures 20 and 21.
#[test]
fn figures_20_and_21_maps() {
    assert_eq!(
        render(b"H\x91\x03fee\xa0\x03fie\xc9\x00\x03foeZ"),
        r#"{1="fee", 16="fie", 256="foe"}"#
    );
    let car = concat(&[
        b"M\x13com.caucho.test.Car",
        b"\x05color\x0aaquamarine\x05model\x06Beetle\x07mileageI\x00\x01\x00\x00Z",
    ]);
    assert_eq!(
        render(&car),
        r#"{com.caucho.test.Car:"color"="aquamarine", "model"="Beetle", "mileage"=65536}"#
    );
}

/// §4.10.3 Figure 24.
#[test]
fn figure_24_objects() {
    let bytes =
        b"C\x0bexample.Car\x92\x05color\x05modelO\x90\x03red\x08corvette\x60\x05green\x05civic";
    assert_eq!(
        render(bytes),
        r#"example.Car(color="red", model="corvette") example.Car(color="green", model="civic")"#
    );
}

/// §4.10.3 Figure 25, without the stray `x90` in the second object.
#[test]
fn figure_25_enum() {
    let bytes = b"C\x0dexample.Color\x91\x04name\x60\x03RED\x60\x05GREEN\x60\x04BLUE\x51\x91";
    assert_eq!(
        render(bytes),
        r#"example.Color(name="RED") example.Color(name="GREEN") example.Color(name="BLUE") @1"#
    );
}

/// §4.11.1 Figure 27, with the Hessian 2.0 object tag `O` instead of `o`.
#[test]
fn figure_27_circular_list() {
    let bytes = b"C\x0aLinkedList\x92\x04head\x04tailO\x90\x91\x51\x90";
    assert_eq!(render(bytes), "LinkedList(head=1, tail=@0)");
}

/// §4.12.2 Figure 29.
#[test]
fn figure_29_strings() {
    assert_eq!(
        render(b"\x00\x05hello\x01\xc3\x83S\x00\x05hello"),
        r#""" "hello" "Ã" "hello""#
    );
    assert_eq!(render(b"\x52\x00\x07hello, \x05world"), r#""hello, world""#);
}

/// The writer produces the specification's forms for the examples above.
#[test]
fn writer_matches_examples() {
    let mut w = Writer::new();
    let class = w.define_class("example.Car", &["color", "model"]);
    for (color, model) in [("red", "corvette"), ("green", "civic")] {
        w.begin_object(class);
        w.write_str(color);
        w.write_str(model);
    }
    // The writer uses x60 for both objects where Figure 24 shows O x90 first.
    assert_eq!(
        w.as_bytes(),
        b"C\x0bexample.Car\x92\x05color\x05model\x60\x03red\x08corvette\x60\x05green\x05civic"
    );

    let mut w = Writer::new();
    w.begin_list(2, Some("[int"));
    w.write_int(0);
    w.write_int(1);
    w.begin_list(3, Some("[int"));
    (2..5).for_each(|n| w.write_int(n));
    assert_eq!(w.as_bytes(), b"\x72\x04[int\x90\x91\x73\x90\x92\x93\x94");

    let mut w = Writer::new();
    w.begin_map(None);
    for (key, value) in [(1, "fee"), (16, "fie"), (256, "foe")] {
        w.write_int(key);
        w.write_str(value);
    }
    w.end_map();
    assert_eq!(w.as_bytes(), b"H\x91\x03fee\xa0\x03fie\xc9\x00\x03foeZ");
}
