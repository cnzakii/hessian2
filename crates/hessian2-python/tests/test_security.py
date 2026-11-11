"""Defensive local regressions; callbacks only record events or mutate fixtures."""

import gc
import subprocess
import sys
import sysconfig
import textwrap
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, make_dataclass
from enum import Enum
from threading import Event

import hessian2
import pytest


@pytest.mark.skipif(
    not sysconfig.get_config_var("Py_GIL_DISABLED") or not hasattr(sys, "_is_immortal"),
    reason="requires free-threaded CPython with sys._is_immortal",
)
def test_peer_field_names_are_not_immortal():
    field = "review_field_" + uuid.uuid4().hex
    wire = hessian2.dumps(hessian2.HessianObject("Unregistered", {field: None}))
    decoded = hessian2.Codec(unknown_class="dict").loads(wire)
    key = next(iter(decoded))
    assert key == field
    assert not sys._is_immortal(key)


@pytest.mark.skipif(
    not sysconfig.get_config_var("Py_GIL_DISABLED")
    or not hasattr(sys, "getunicodeinternedsize"),
    reason="requires free-threaded CPython with an observable intern table",
)
def test_rejected_class_does_not_grow_intern_table():
    field = "review_rejected_" + uuid.uuid4().hex
    wire = hessian2.dumps(hessian2.HessianObject("Unregistered", {field: None}))
    before = sys.getunicodeinternedsize()
    with pytest.raises(hessian2.DecodeError, match="not registered"):
        hessian2.loads(wire)
    gc.collect()
    assert sys.getunicodeinternedsize() == before


def test_registry_replacement_finalizer_can_reenter_codec():
    program = """
        from dataclasses import dataclass
        import hessian2
        codec = hessian2.Codec()
        @dataclass
        class A:
            x: int = 0
        @dataclass
        class B:
            x: int = 0
        class Encode:
            def __call__(self, value):
                return {'x': value.x}
            def __del__(self):
                codec.loads(b'N')
        codec.register('X', hessian2.Converter(A, Encode(), lambda f: A(**f)))
        codec.register('X', B, replace=True)
        print('ok')
    """
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(program)],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    assert result.stdout.strip() == "ok"


def test_invalid_enum_name_does_not_invoke_object_methods():
    codec = hessian2.Codec()
    events = []

    @codec.register("E")
    class E(Enum):
        A = 1

    @codec.register("Name")
    @dataclass(eq=False)
    class Name:
        value: int

        def __hash__(self):
            events.append("hash")
            return hash("A")

        def __eq__(self, other):
            events.append("eq")
            return False

        def __str__(self):
            events.append("str")
            return "name"

    wire = hessian2.dumps(
        hessian2.HessianObject(
            "E", {"name": hessian2.HessianObject("Name", {"value": 1})}
        )
    )
    with pytest.raises(hessian2.DecodeError):
        codec.loads(wire)
    assert events == []


def test_mutated_map_does_not_raise_a_rust_panic():
    codec = hessian2.Codec()

    @dataclass
    class Value:
        x: int

    values = {"value": Value(1)}

    def encode(value):
        values["added"] = 2
        return {"x": value.x}

    codec.register("Value", hessian2.Converter(Value, encode, lambda f: Value(**f)))
    try:
        wire = codec.dumps(values)
    except BaseException as error:
        assert isinstance(error, Exception), type(error).__name__
    else:
        assert set(codec.loads(wire)) == {"value"}


def test_registered_hooks_are_part_of_the_decode_trust_boundary():
    codec = hessian2.Codec()
    events = []

    @codec.register("Key")
    @dataclass(eq=False)
    class Key:
        value: int

        def __post_init__(self):
            events.append("post_init")

        def __hash__(self):
            events.append("hash")
            return 0

        def __eq__(self, other):
            events.append("eq")
            return self.value == other.value

    first = hessian2.HessianObject("Key", {"value": 1})
    second = hessian2.HessianObject("Key", {"value": 2})
    codec.loads(b"H" + hessian2.dumps_many([first, 0, second, 0]) + b"Z")
    assert {"post_init", "hash", "eq"} <= set(events)


def test_encoding_existing_map_keys_does_not_rehash_them():
    codec = hessian2.Codec()
    events = []

    @codec.register("Key")
    @dataclass(eq=False)
    class Key:
        value: int

        def __hash__(self):
            events.append("hash")
            return 0

        def __eq__(self, other):
            events.append("eq")
            return self.value == other.value

    values = {Key(1): 1, Key(2): 2}
    events.clear()
    codec.dumps(values)
    assert events == []


def test_encode_keeps_temporary_referenced_values_alive():
    codec = hessian2.Codec()
    decoded = codec.loads_many(codec.dumps_many([] for _ in range(256)))
    assert all(value == [] for value in decoded)
    assert len({id(value) for value in decoded}) == 256


def test_list_size_mutation_is_an_ordinary_error():
    codec = hessian2.Codec()

    @dataclass
    class Value:
        x: int

    values = [Value(1)]

    def encode(value):
        values.append(None)
        return {"x": value.x}

    codec.register("Value", hessian2.Converter(Value, encode, lambda f: Value(**f)))
    with pytest.raises(hessian2.EncodeError, match="changed size"):
        codec.dumps(values)


def test_growing_list_stops_before_visiting_appended_elements():
    codec = hessian2.Codec()
    calls = 0

    @dataclass
    class Value:
        x: int

    values = [Value(0)]

    def encode(value):
        nonlocal calls
        calls += 1
        # Bound the reproduction; an always-appending callback never finishes.
        if calls < 8:
            values.append(Value(calls))
        return {"x": value.x}

    codec.register("Value", hessian2.Converter(Value, encode, lambda f: Value(**f)))
    try:
        wire = codec.dumps(values)
    except (hessian2.EncodeError, RuntimeError):
        pass
    else:
        assert len(codec.loads(wire)) == 1
    assert calls == 1


def test_unknown_class_never_imports_its_name():
    name = "review_module_that_does_not_exist"
    wire = hessian2.dumps(hessian2.HessianObject(name + ".Value"))
    with pytest.raises(hessian2.DecodeError, match="not registered"):
        hessian2.loads(wire)
    assert name not in sys.modules
    assert hessian2.Codec(unknown_class="dict").loads(wire) == {}


def test_mutable_input_is_copied_before_callbacks():
    codec = hessian2.Codec()

    @codec.register("Value")
    @dataclass
    class Value:
        x: int

        def __post_init__(self):
            data[:] = b"N" * len(data)

    value = hessian2.HessianObject("Value", {"x": 1})
    data = bytearray(hessian2.dumps_many([value, value]))
    decoded = codec.loads_many(data)
    assert decoded[0].x == 1
    assert decoded[0] is decoded[1]
    assert data == b"N" * len(data)


def test_register_during_loads_uses_one_snapshot():
    codec = hessian2.Codec()

    @dataclass
    class Replacement:
        x: int

    @codec.register("Value")
    @dataclass
    class Original:
        x: int

        def __post_init__(self):
            codec.register("Value", Replacement, replace=True)

    wire = hessian2.dumps_many(
        [
            hessian2.HessianObject("Value", {"x": 1}),
            hessian2.HessianObject("Value", {"x": 2}),
        ]
    )
    decoded = codec.loads_many(wire)
    assert all(type(value) is Original for value in decoded)
    assert all(type(value) is Replacement for value in codec.loads_many(wire))


def test_register_during_dumps_uses_one_snapshot():
    codec = hessian2.Codec()

    @codec.register("Value")
    @dataclass
    class Original:
        x: int

    @dataclass
    class Replacement:
        x: int

    @dataclass
    class Trigger:
        pass

    def encode(_):
        codec.register("Value", Replacement, replace=True)
        return {}

    codec.register("Trigger", hessian2.Converter(Trigger, encode, lambda _: Trigger()))
    wire = codec.dumps_many([Trigger(), Original(1)])
    decoded = hessian2.Codec(unknown_class="dict").loads_many(wire)
    assert decoded == [{}, {"x": 1}]
    with pytest.raises(hessian2.EncodeError):
        codec.dumps(Original(1))


def test_concurrent_registration_preserves_independent_entries():
    codec = hessian2.Codec()
    classes = [make_dataclass(f"Value{i}", [("x", int)]) for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda pair: codec.register(f"Value{pair[0]}", pair[1], replace=True),
                enumerate(classes),
            )
        )
    for cls in classes:
        value = codec.loads(codec.dumps(cls(1)))
        assert type(value) is cls
        assert value.x == 1


def test_concurrent_replacement_does_not_change_an_inflight_decode():
    codec = hessian2.Codec()
    entered, release = Event(), Event()

    @dataclass
    class Replacement:
        x: int

    @codec.register("Value")
    @dataclass
    class Original:
        x: int

        def __post_init__(self):
            entered.set()
            assert release.wait(5)

    wire = hessian2.dumps_many(
        [
            hessian2.HessianObject("Value", {"x": 1}),
            hessian2.HessianObject("Value", {"x": 2}),
        ]
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(codec.loads_many, wire)
        try:
            assert entered.wait(5)
            codec.register("Value", Replacement, replace=True)
        finally:
            release.set()
        assert all(type(value) is Original for value in result.result(timeout=5))
    assert all(type(value) is Replacement for value in codec.loads_many(wire))


@pytest.mark.parametrize(
    "kind", ["converter", "factory", "default", "class", "initvar"]
)
def test_codec_registration_cycles_are_collected(kind):
    import weakref
    from dataclasses import InitVar, field

    def build():
        codec = hessian2.Codec()
        if kind == "converter":

            class Value:
                pass

            def to_fields(value):
                return {"value": codec.max_depth}

            codec.register(
                "Value", hessian2.Converter(Value, to_fields, lambda f: Value())
            )
        elif kind == "factory":

            @dataclass
            class Value:
                value: object = field(default_factory=lambda: codec)

            codec.register("Value", Value)
        elif kind == "default":

            @dataclass
            class Value:
                value: object = codec

            codec.register("Value", Value)
        elif kind == "initvar":

            @dataclass
            class Value:
                value: InitVar[object] = codec

                def __post_init__(self, value):
                    pass

            codec.register("Value", Value)
        else:

            @codec.register("Value")
            @dataclass
            class Value:
                def owner(self):
                    return codec

        return weakref.ref(codec), weakref.ref(Value)

    codec_ref, class_ref = build()
    gc.collect()
    assert codec_ref() is None
    assert class_ref() is None


def test_gc_keeps_the_old_registration_alive_during_a_call():
    import weakref

    codec = hessian2.Codec()
    seen = []

    class Original:
        pass

    @dataclass
    class Replacement:
        value: int

    class ToFields:
        def __call__(self, value):
            codec.register("Value", Replacement, replace=True)
            gc.collect()
            assert callback_ref() is self
            seen.append(value)
            return {"value": 1}

    callback = ToFields()
    callback_ref = weakref.ref(callback)
    codec.register(
        "Value", hessian2.Converter(Original, callback, lambda f: Original())
    )
    del callback
    value = Original()
    wire = codec.dumps_many([value, value])
    assert seen == [value]
    gc.collect()
    assert callback_ref() is None
    first, second = codec.loads_many(wire)
    assert first is second and first == Replacement(1)


def test_codecs_sharing_registrations_are_collected_without_double_counting():
    import weakref

    def build():
        first = hessian2.Codec()
        second = hessian2.Codec()

        @dataclass
        class Value:
            left: object = first
            right: object = second

        first.register("Value", Value)
        second.register("Value", Value)
        first.register("Alias", Value, replace=True)
        return weakref.ref(first), weakref.ref(second), weakref.ref(Value)

    refs = build()
    gc.collect()
    assert all(ref() is None for ref in refs)
