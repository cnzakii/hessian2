package interop;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.Serializable;
import java.lang.reflect.Array;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Collection;
import java.util.Date;
import java.util.Hashtable;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.TreeMap;
import java.util.UUID;
import java.util.Vector;
import java.util.concurrent.ConcurrentHashMap;

import com.caucho.hessian.test.TestHessian2Servlet;

/**
 * Produces and checks Hessian bytes with Caucho's reference implementation
 * ("caucho") and hessian-lite ("lite").
 *
 * <pre>
 * write OUT.json        values of every case, as bytes from both writers and
 *                       as a structural description
 * describe READER DIR   reads each DIR/NAME.bin with READER and prints
 *                       {NAME: description or {"error": message}}; suite values also run arg*
 * inventory            all arg* methods and their matching reply* names
 * types READER DIR      reads each DIR/NAME.bin as the Java type DIR/NAME.type
 *                       names, as Java code reads a value of a declared type,
 *                       and prints {NAME: the Java classes it holds}
 * </pre>
 */
public final class Interop {
    public static void main(String[] args) throws Exception {
        switch (args[0]) {
            case "write" -> write(Path.of(args[1]));
            case "describe" -> describe(args[1], Path.of(args[2]));
            case "inventory" -> inventory();
            case "types" -> types(args[1], Path.of(args[2]));
            default -> throw new IllegalArgumentException(args[0]);
        }
    }

    /** Every case: Caucho's conformance suite plus {@link Extras}. */
    static Map<String, List<Object>> cases() throws Exception {
        Map<String, List<Object>> cases = new TreeMap<>();
        TestHessian2Servlet servlet = new TestHessian2Servlet();
        for (Method method : TestHessian2Servlet.class.getMethods()) {
            if (method.getName().startsWith("reply") && method.getParameterCount() == 0) {
                cases.put("suite." + method.getName(), List.of(nullable(method.invoke(servlet))));
            }
        }
        for (Method method : Extras.class.getDeclaredMethods()) {
            if (Modifier.isStatic(method.getModifiers()) && method.getParameterCount() == 0) {
                Object value = method.invoke(null);
                @SuppressWarnings("unchecked")
                List<Object> values = method.getName().startsWith("stream")
                    ? (List<Object>) value : List.of(nullable(value));
                cases.put("extra." + method.getName(), values);
            }
        }
        return cases;
    }

    /** Keep the reply/arg inventory executable so upgrades expose omissions. */
    static void inventory() throws Exception {
        Map<String, String> methods = new TreeMap<>();
        for (Method method : TestHessian2Servlet.class.getMethods()) {
            if (method.getName().startsWith("arg") && method.getParameterCount() == 1) {
                String reply = "reply" + method.getName().substring(3);
                TestHessian2Servlet.class.getMethod(reply);
                methods.put(method.getName(), reply);
            }
        }
        StringBuilder out = new StringBuilder("{");
        boolean first = true;
        for (var entry : methods.entrySet()) {
            out.append(first ? "" : ",").append(Json.string(entry.getKey())).append(":")
                .append(Json.string(entry.getValue()));
            first = false;
        }
        System.out.println(out.append("}"));
    }

    private static final Object NULL = new Object();

    private static Object nullable(Object value) {
        return value == null ? NULL : value;
    }

    static void write(Path out) throws Exception {
        StringBuilder json = new StringBuilder("{\n");
        boolean first = true;
        for (Map.Entry<String, List<Object>> entry : cases().entrySet()) {
            List<Object> values = new ArrayList<>();
            for (Object value : entry.getValue()) {
                values.add(value == NULL ? null : value);
            }
            json.append(first ? "" : ",\n").append(Json.string(entry.getKey())).append(": {");
            first = false;
            json.append("\"values\": [");
            for (int i = 0; i < values.size(); i++) {
                json.append(i == 0 ? "" : ", ").append(Describe.of(values.get(i)));
            }
            byte[] caucho;
            try {
                caucho = caucho(values);
            } catch (RuntimeException error) {
                // Caucho cannot write java.time values on JDK 17: java.base
                // does not open their private fields to reflection.
                caucho = null;
            }
            byte[] lite = lite(values);
            json.append("], \"caucho\": ").append(caucho == null ? "null" : Json.string(base64(caucho)));
            // null: hessian-lite wrote the same bytes.
            json.append(", \"lite\": ").append(
                java.util.Arrays.equals(caucho, lite) ? "null" : Json.string(base64(lite)));
            json.append("}");
        }
        Files.writeString(out, json.append("\n}\n").toString());
    }

    static void describe(String reader, Path dir) throws Exception {
        StringBuilder json = new StringBuilder("{");
        boolean first = true;
        try (var files = Files.list(dir)) {
            for (Path file : files.sorted().toList()) {
                String name = file.getFileName().toString();
                if (!name.endsWith(".bin")) {
                    continue;
                }
                byte[] bytes = Files.readAllBytes(file);
                String description;
                try {
                    StringBuilder values = new StringBuilder("[");
                    List<Object> read = reader.equals("caucho") ? readCaucho(bytes) : readLite(bytes);
                    for (int i = 0; i < read.size(); i++) {
                        values.append(i == 0 ? "" : ", ").append(Describe.of(read.get(i)));
                    }
                    String arg = "";
                    if (name.startsWith("suite.reply") && !name.equals("suite.replyString_null.bin")
                        && !name.equals("suite.replyBinary_null.bin")) {
                        Method check = TestHessian2Servlet.class.getMethod(
                            "arg" + name.substring("suite.reply".length(), name.length() - 4), Object.class);
                        Object result = check.invoke(new TestHessian2Servlet(), read.get(0));
                        arg = ", \"arg\": " + Boolean.TRUE.equals(result);
                    }
                    description = "{\"values\": " + values.append("]") + arg + "}";
                } catch (Throwable error) {
                    description = "{\"error\": " + Json.string(String.valueOf(error)) + "}";
                }
                json.append(first ? "" : ",\n").append(Json.string(name.substring(0, name.length() - 4)))
                    .append(": ").append(description);
                first = false;
            }
        }
        System.out.println(json.append("}"));
    }

    /** What Java types a value read as a declared type holds: its class, item and value classes, fields. */
    static void types(String reader, Path dir) throws Exception {
        StringBuilder json = new StringBuilder("{");
        boolean first = true;
        try (var files = Files.list(dir)) {
            for (Path file : files.sorted().toList()) {
                String name = file.getFileName().toString();
                if (!name.endsWith(".bin")) {
                    continue;
                }
                name = name.substring(0, name.length() - 4);
                Class<?> type = Class.forName(Files.readString(dir.resolve(name + ".type")).strip());
                ByteArrayInputStream input = new ByteArrayInputStream(Files.readAllBytes(file));
                String description;
                try {
                    Object value = reader.equals("caucho")
                        ? new com.caucho.hessian.io.Hessian2Input(input).readObject(type)
                        : new com.alibaba.com.caucho.hessian.io.Hessian2Input(input).readObject(type);
                    description = Types.of(value);
                } catch (Throwable error) {
                    description = "{\"error\": " + Json.string(String.valueOf(error)) + "}";
                }
                json.append(first ? "" : ",\n").append(Json.string(name)).append(": ").append(description);
                first = false;
            }
        }
        System.out.println(json.append("}"));
    }

    static String base64(byte[] bytes) {
        return Base64.getEncoder().encodeToString(bytes);
    }

    static byte[] caucho(List<Object> values) throws IOException {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        com.caucho.hessian.io.Hessian2Output out = new com.caucho.hessian.io.Hessian2Output(bytes);
        for (Object value : values) {
            out.writeObject(value);
        }
        out.close();
        return bytes.toByteArray();
    }

    static byte[] lite(List<Object> values) throws IOException {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        com.alibaba.com.caucho.hessian.io.Hessian2Output out =
            new com.alibaba.com.caucho.hessian.io.Hessian2Output(bytes);
        for (Object value : values) {
            out.writeObject(value);
        }
        out.close();
        return bytes.toByteArray();
    }

    static List<Object> readCaucho(byte[] bytes) throws IOException {
        ByteArrayInputStream input = new ByteArrayInputStream(bytes);
        com.caucho.hessian.io.Hessian2Input in = new com.caucho.hessian.io.Hessian2Input(input);
        List<Object> values = new ArrayList<>();
        while (!in.isEnd()) {
            values.add(in.readObject());
        }
        return values;
    }

    static List<Object> readLite(byte[] bytes) throws IOException {
        ByteArrayInputStream input = new ByteArrayInputStream(bytes);
        com.alibaba.com.caucho.hessian.io.Hessian2Input in =
            new com.alibaba.com.caucho.hessian.io.Hessian2Input(input);
        List<Object> values = new ArrayList<>();
        while (!in.isEnd()) {
            values.add(in.readObject());
        }
        return values;
    }
}

/**
 * A JSON description of a Java value that a Python decoder can reproduce:
 * integers of every width are {"int"}, floating point values {"double"} with
 * their bit pattern, arrays and collections {"list"}, and other objects
 * {"object"} with their serialized fields. A list, map, or object seen before
 * is {"ref": n}, numbered in first-visit order.
 */
final class Describe {
    /** Strings (in UTF-16 units) and binaries longer than this are described by SHA-256. */
    static final int LARGE = 1024;

    private final IdentityHashMap<Object, Integer> seen = new IdentityHashMap<>();

    static String sha256(byte[] bytes) {
        try {
            return java.util.HexFormat.of().formatHex(
                java.security.MessageDigest.getInstance("SHA-256").digest(bytes));
        } catch (java.security.NoSuchAlgorithmException error) {
            throw new IllegalStateException(error);
        }
    }

    static String of(Object value) {
        return new Describe().value(value);
    }

    private String value(Object value) {
        if (value == null) {
            return "null";
        } else if (value instanceof Boolean b) {
            return "{\"bool\": " + b + "}";
        } else if (value instanceof Byte || value instanceof Short || value instanceof Integer
            || value instanceof Long) {
            return "{\"int\": \"" + value + "\"}";
        } else if (value instanceof Float || value instanceof Double) {
            long bits = Double.doubleToRawLongBits(((Number) value).doubleValue());
            return "{\"double\": \"" + Long.toHexString(bits) + "\"}";
        } else if (value instanceof String s) {
            if (s.length() > LARGE) {
                return "{\"str_sha256\": \"" + sha256(s.getBytes(java.nio.charset.StandardCharsets.UTF_16LE)) + "\"}";
            }
            return "{\"str\": " + Json.string(s) + "}";
        } else if (value instanceof char[] chars) {
            return value(new String(chars));
        } else if (value instanceof Character c) {
            return "{\"str\": " + Json.string(String.valueOf(c)) + "}";
        } else if (value instanceof byte[] bytes) {
            if (bytes.length > LARGE) {
                return "{\"bin_sha256\": \"" + sha256(bytes) + "\"}";
            }
            return "{\"bin\": " + Json.string(Interop.base64(bytes)) + "}";
        } else if (value instanceof Date date) {
            return "{\"date\": \"" + date.getTime() + "\"}";
        } else if (value instanceof BigDecimal decimal) {
            return "{\"decimal\": " + Json.string(decimal.toString()) + "}";
        } else if (value instanceof UUID uuid) {
            return "{\"uuid\": " + Json.string(uuid.toString()) + "}";
        } else if (value instanceof java.math.BigInteger big) {
            return "{\"int\": \"" + big + "\"}";
        } else if (value instanceof java.time.LocalDate date) {
            return "{\"local_date\": \"" + localDate(date) + "\"}";
        } else if (value instanceof java.time.LocalTime time) {
            return "{\"local_time\": \"" + localTime(time) + "\"}";
        } else if (value instanceof java.time.LocalDateTime dateTime) {
            return "{\"local_datetime\": \"" + localDate(dateTime.toLocalDate()) + "T"
                + localTime(dateTime.toLocalTime()) + "\"}";
        } else if (value instanceof java.time.Duration duration) {
            return "{\"duration\": \"" + duration.getSeconds() + "."
                + String.format("%06d", duration.getNano() / 1000) + "\"}";
        } else if (value instanceof java.time.Instant instant) {
            return "{\"date\": \"" + instant.toEpochMilli() + "\"}";
        } else if (value instanceof java.time.OffsetDateTime dateTime) {
            return "{\"date\": \"" + dateTime.toInstant().toEpochMilli() + "\"}";
        } else if (value instanceof java.time.ZonedDateTime dateTime) {
            return "{\"date\": \"" + dateTime.toInstant().toEpochMilli() + "\"}";
        }
        Integer ref = seen.get(value);
        if (ref != null) {
            return "{\"ref\": " + ref + "}";
        }
        seen.put(value, seen.size());
        if (value.getClass().isArray()) {
            List<Object> items = new ArrayList<>();
            for (int i = 0; i < Array.getLength(value); i++) {
                items.add(Array.get(value, i));
            }
            return list(items);
        } else if (value instanceof Collection<?> items) {
            return list(items);
        } else if (value instanceof Map<?, ?> map) {
            StringBuilder out = new StringBuilder("{\"map\": [");
            boolean first = true;
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                out.append(first ? "" : ", ").append("[").append(value(entry.getKey())).append(", ")
                    .append(value(entry.getValue())).append("]");
                first = false;
            }
            return out.append("]}").toString();
        } else if (value instanceof Enum<?> e) {
            return "{\"object\": " + Json.string(e.getDeclaringClass().getName())
                + ", \"fields\": {\"name\": {\"str\": " + Json.string(e.name()) + "}}}";
        }
        if (value instanceof Throwable error) {
            return "{\"object\": " + Json.string(value.getClass().getName())
                + ", \"fields\": {\"detailMessage\": " + value(error.getMessage())
                + ", \"cause\": " + value(error.getCause())
                + ", \"stackTrace\": " + value(error.getStackTrace())
                + ", \"suppressedExceptions\": " + value(List.of(error.getSuppressed())) + "}}";
        }
        return object(value);
    }

    /** Python's datetime keeps microseconds, so the descriptions stop there. */
    private static String localDate(java.time.LocalDate date) {
        return String.format("%04d-%02d-%02d", date.getYear(), date.getMonthValue(), date.getDayOfMonth());
    }

    private static String localTime(java.time.LocalTime time) {
        return String.format("%02d:%02d:%02d.%06d", time.getHour(), time.getMinute(), time.getSecond(),
            time.getNano() / 1000);
    }

    private String list(Collection<?> items) {
        StringBuilder out = new StringBuilder("{\"list\": [");
        boolean first = true;
        for (Object item : items) {
            out.append(first ? "" : ", ").append(value(item));
            first = false;
        }
        return out.append("]}").toString();
    }

    /** Fields as Caucho's JavaSerializer selects them: non-static, non-transient, superclasses included. */
    private String object(Object value) {
        Map<String, Object> fields = new LinkedHashMap<>();
        for (Class<?> cls = value.getClass(); cls != null && cls != Object.class; cls = cls.getSuperclass()) {
            for (Field field : cls.getDeclaredFields()) {
                int modifiers = field.getModifiers();
                if (Modifier.isStatic(modifiers) || Modifier.isTransient(modifiers)) {
                    continue;
                }
                try {
                    field.setAccessible(true);
                    fields.putIfAbsent(field.getName(), field.get(value));
                } catch (ReflectiveOperationException | RuntimeException error) {
                    throw new IllegalStateException("cannot describe " + value.getClass(), error);
                }
            }
        }
        StringBuilder out = new StringBuilder("{\"object\": " + Json.string(value.getClass().getName()) + ", \"fields\": {");
        boolean first = true;
        for (Map.Entry<String, Object> field : fields.entrySet()) {
            out.append(first ? "" : ", ").append(Json.string(field.getKey())).append(": ").append(value(field.getValue()));
            first = false;
        }
        return out.append("}}").toString();
    }
}

/** The Java classes a value holds, for checking which types a peer received. */
final class Types {
    static String of(Object value) {
        if (value == null) {
            return "null";
        }
        StringBuilder out = new StringBuilder("{\"class\": " + Json.string(value.getClass().getName()));
        if (value instanceof Collection<?> items) {
            out.append(", \"items\": ").append(classes(items));
        } else if (value instanceof Map<?, ?> map) {
            out.append(", \"values\": ").append(classes(map.values()));
        } else if (value instanceof Throwable error) {
            out.append(", \"message\": ").append(Json.string(String.valueOf(error.getMessage())));
        } else if (value instanceof Typed) {
            out.append(", \"fields\": {");
            boolean first = true;
            for (Field field : Typed.class.getDeclaredFields()) {
                try {
                    out.append(first ? "" : ", ").append(Json.string(field.getName())).append(": ")
                        .append(of(field.get(value)));
                } catch (IllegalAccessException error) {
                    throw new IllegalStateException(error);
                }
                first = false;
            }
            out.append("}");
        }
        return out.append("}").toString();
    }

    private static String classes(Collection<?> items) {
        StringBuilder out = new StringBuilder("[");
        boolean first = true;
        for (Object item : items) {
            out.append(first ? "" : ", ").append(Json.string(item == null ? "null" : item.getClass().getName()));
            first = false;
        }
        return out.append("]").toString();
    }
}

/** Fields of the Java types that do not convert from Python's default forms. */
class Typed implements Serializable {
    java.math.BigInteger amount;
    List<java.math.BigInteger> amounts;
    java.time.Instant instant;
    java.time.OffsetDateTime offset;
    java.time.ZonedDateTime zoned;
    List<Long> ids;
    Map<String, Object> extra;
    Object any;
}

final class Json {
    /** A JSON string with every non-ASCII UTF-16 unit escaped, so unpaired surrogates survive. */
    static String string(String text) {
        StringBuilder out = new StringBuilder("\"");
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (c == '"' || c == '\\') {
                out.append('\\').append(c);
            } else if (c < 0x20 || c > 0x7e) {
                out.append(String.format("\\u%04x", (int) c));
            } else {
                out.append(c);
            }
        }
        return out.append('"').toString();
    }
}

enum Color {
    RED, GREEN, BLUE
}

class Person implements Serializable {
    String name;
    int age;
    long id;
    double score;
    boolean active;
    Date born;
    List<String> tags;
    Map<String, Integer> attrs;
    Person friend;
    Color color;
    BigDecimal balance;
    byte[] avatar;
}

class ShadowParent implements Serializable {
    String same = "parent";
}

class ShadowChild extends ShadowParent {
    String same = "child";
}

class Employee extends Person {
    String company;
}

/** Cases the conformance suite does not cover. */
final class Extras {
    static List<Object> collection_types() {
        List<Object> result = new ArrayList<>();
        result.add(new LinkedList<>(java.util.Arrays.asList(null, 3, "tail")));
        result.add(new Vector<>(List.of(1, 2)));
        result.add(new LinkedHashSet<>(List.of("first", "second")));
        result.add(new java.util.TreeSet<>(List.of("a", "b")));
        result.add(new java.util.HashSet<>(List.of("only")));
        return result;
    }

    static List<Object> map_types() {
        List<Object> result = new ArrayList<>();
        result.add(new java.util.HashMap<>(Map.of("hash", 1)));
        result.add(new LinkedHashMap<>(Map.of("linked", 2)));
        result.add(new TreeMap<>(Map.of("sorted", 3)));
        result.add(new Hashtable<>(Map.of("table", 4)));
        Properties properties = new Properties();
        properties.setProperty("property", "value");
        result.add(properties);
        result.add(new ConcurrentHashMap<>(Map.of("concurrent", 5)));
        return result;
    }

    static List<Object> array_types() {
        return new ArrayList<>(java.util.Arrays.asList(
            new boolean[] {true, false}, new short[] {Short.MIN_VALUE, Short.MAX_VALUE},
            new long[] {Long.MIN_VALUE, Long.MAX_VALUE}, new double[] {0.001, Double.MIN_VALUE},
            new float[] {0.5f, 1.0f}, new char[] {'a', '中'},
            new Boolean[] {null, true, false}, new Character[] {null, 'a', '中'},
            new Long[] {null, 1L << 40}, new byte[][] {{0, -1}, {}, null},
            new Integer[] {null, -1, Integer.MAX_VALUE}, new String[] {null, "中", "😀"},
            new int[][] {{}, {1, 2}}, new Object[] {Color.RED, new BigDecimal("1.00"), null}));
    }

    static Object[] array_cycle() {
        Object[] array = new Object[2];
        array[0] = "self";
        array[1] = array;
        return array;
    }

    static List<Object> mutual_beans() {
        Person first = new Person();
        Person second = new Person();
        first.name = "first";
        second.name = "second";
        first.friend = second;
        second.friend = first;
        return new ArrayList<>(List.of(first, second, first));
    }

    static ShadowChild shadowed_fields() {
        return new ShadowChild();
    }

    static Throwable throwable() {
        // shortcut: traces are empty; add JDK-specific StackTraceElement descriptions for populated traces.
        Exception cause = new Exception("cause", null);
        cause.setStackTrace(new StackTraceElement[0]);
        Exception error = new Exception("消息", cause);
        error.setStackTrace(new StackTraceElement[0]);
        return error;
    }

    /** SOFA's generic-object buffer-boundary regression, using ordinary beans. */
    static Person large_nested_bean() {
        Person person = new Person();
        person.name = "V".repeat(8140);
        person.friend = new Person();
        person.friend.name = "nested";
        return person;
    }

    static List<Object> stream_collections() {
        List<Object> shared = new LinkedList<>(java.util.Arrays.asList(null, "tail"));
        return java.util.Arrays.asList(shared, 123, shared, null, "end");
    }

    static List<Object> list_of_lists_shared() {
        List<Object> inner = new ArrayList<>(List.of("item"));
        return new ArrayList<>(List.of(inner, inner));
    }

    static String string_surrogate_pair() {
        return "a😀b";
    }

    static String string_lone_high_surrogate() {
        return "x\ud83dy";
    }

    static String string_lone_low_surrogate() {
        return "x\ude00y";
    }

    static String string_cjk() {
        return "中文测试";
    }

    /** A pair straddling the first 0x8000-unit chunk boundary. */
    static String string_chunk_surrogate() {
        return "a".repeat(0x7fff) + "😀" + "b".repeat(10);
    }

    static String string_chunk_exact() {
        return "c".repeat(0x8000);
    }

    static String string_long_emoji() {
        return "😀".repeat(17000);
    }

    static double double_negative_zero() {
        return -0.0;
    }

    static double double_mill() {
        return 0.003;
    }

    static double double_nan() {
        return Double.NaN;
    }

    static java.time.LocalDate time_local_date() {
        return java.time.LocalDate.of(2024, 2, 29);
    }

    static java.time.LocalTime time_local_time() {
        return java.time.LocalTime.of(13, 45, 30, 123_456_000);
    }

    /** Nanoseconds below a microsecond, which Python drops. */
    static java.time.LocalTime time_local_time_nanos() {
        return java.time.LocalTime.of(0, 0, 0, 999);
    }

    static java.time.LocalDateTime time_local_datetime() {
        return java.time.LocalDateTime.of(1999, 12, 31, 23, 59, 59, 999_999_000);
    }

    static java.time.Duration time_duration() {
        return java.time.Duration.ofSeconds(90061, 5000);
    }

    static java.time.Duration time_duration_negative() {
        return java.time.Duration.ofMillis(-1500);
    }

    static java.time.Instant time_instant() {
        return java.time.Instant.ofEpochMilli(1_700_000_000_123L);
    }

    static java.time.OffsetDateTime time_offset_datetime() {
        return java.time.OffsetDateTime.of(2024, 2, 29, 13, 45, 30, 0, java.time.ZoneOffset.ofHours(8));
    }

    static java.time.ZonedDateTime time_zoned_datetime() {
        return java.time.ZonedDateTime.of(2024, 7, 1, 9, 0, 0, 0, java.time.ZoneId.of("Europe/Paris"));
    }

    /** The same date twice: the second is a back-reference. */
    static List<Object> time_shared_date() {
        java.time.LocalDate date = java.time.LocalDate.of(2000, 1, 1);
        return new ArrayList<>(List.of(date, date));
    }

    static java.math.BigInteger bigint_large() {
        return new java.math.BigInteger("-123456789012345678901234567890");
    }

    static java.math.BigInteger bigint_word_boundary() {
        return java.math.BigInteger.ONE.shiftLeft(64);
    }

    /** The Java types Python writes only with tags, in a class's fields. */
    static Typed typed_fields() {
        Typed typed = typed_without_time();
        typed.instant = time_instant();
        typed.offset = time_offset_datetime();
        typed.zoned = time_zoned_datetime();
        return typed;
    }

    /** {@link #typed_fields} without java.time, which Caucho cannot write on JDK 17. */
    static Typed typed_without_time() {
        Typed typed = new Typed();
        typed.amount = java.math.BigInteger.valueOf(5);
        typed.amounts = new ArrayList<>(List.of(java.math.BigInteger.valueOf(6)));
        typed.ids = new ArrayList<>(List.of(7L));
        typed.extra = new java.util.HashMap<>(Map.of("count", 8L));
        typed.any = 9L;
        return typed;
    }

    static java.math.BigInteger bigint_small() {
        return java.math.BigInteger.valueOf(-5);
    }

    static java.math.BigInteger bigint_zero() {
        return java.math.BigInteger.ZERO;
    }

    static java.util.Set<String> set_hash() {
        return new java.util.HashSet<>(List.of("a"));
    }

    static Object boxed_byte() {
        return Byte.valueOf((byte) -3);
    }

    static Object boxed_short() {
        return Short.valueOf((short) 300);
    }

    static Object boxed_float() {
        return Float.valueOf(1.5f);
    }

    static double double_infinity() {
        return Double.NEGATIVE_INFINITY;
    }

    static double double_max() {
        return Double.MAX_VALUE;
    }


    static Date date_minute() {
        return new Date(1_700_000_040_000L);
    }

    static Date date_millis() {
        return new Date(1_700_000_000_123L);
    }

    static Date date_before_epoch() {
        return new Date(-86_400_001L);
    }

    static long long_small() {
        return 5L;
    }

    static int[] int_array() {
        return new int[] {1, 2, 3};
    }

    static String[] string_array() {
        return new String[] {"a", "b"};
    }

    static BigDecimal decimal() {
        return new BigDecimal("-123.4500");
    }

    static BigDecimal decimal_exponent() {
        return new BigDecimal("1E+3");
    }

    static UUID uuid() {
        return UUID.fromString("123e4567-e89b-12d3-a456-426614174000");
    }

    static List<Object> enum_repeated() {
        return new ArrayList<>(List.of(Color.RED, Color.GREEN, Color.RED));
    }

    static Person person() {
        Person friend = new Person();
        friend.name = "Bob";
        Person person = new Person();
        person.name = "Alice";
        person.age = 30;
        person.id = 1L << 40;
        person.score = 98.5;
        person.active = true;
        person.born = new Date(631_152_000_000L);
        person.tags = new ArrayList<>(List.of("x", "y"));
        person.attrs = new LinkedHashMap<>(Map.of("k", 1));
        person.friend = friend;
        person.color = Color.BLUE;
        person.balance = new BigDecimal("10.50");
        person.avatar = new byte[] {1, 2, 3};
        return person;
    }

    static Employee employee() {
        Employee employee = new Employee();
        employee.name = "Carol";
        employee.company = "Example";
        return employee;
    }

    static List<Object> list_cycle() {
        List<Object> list = new ArrayList<>();
        list.add(1);
        list.add(list);
        return list;
    }

    static Map<String, Object> map_cycle() {
        Map<String, Object> map = new LinkedHashMap<>();
        map.put("self", map);
        return map;
    }

    static Person object_cycle() {
        Person person = new Person();
        person.name = "Self";
        person.friend = person;
        return person;
    }

    static Map<Object, Object> map_mixed_keys() {
        Map<Object, Object> map = new LinkedHashMap<>();
        map.put(1, "int");
        map.put("s", "str");
        map.put(2.5, "double");
        return map;
    }

    /** Several top-level values in one stream share class definitions and references. */
    static List<Object> stream_shared() {
        Person shared = new Person();
        shared.name = "Shared";
        Employee employee = new Employee();
        employee.name = "Dan";
        employee.friend = shared;
        return List.of(shared, employee, "tail");
    }

    static List<Object> review2_time_nested_refs() {
        java.time.LocalDateTime local = java.time.LocalDateTime.of(2024, 2, 29, 12, 34, 56, 123456000);
        List<Object> tail = new ArrayList<>(List.of("tail"));
        return new ArrayList<>(List.of(local, local.toLocalTime(), local.toLocalDate(), local,
            local.toLocalTime(), local.toLocalDate(), tail, tail));
    }

    static List<Object> review2_bigint_refs() {
        java.math.BigInteger big = java.math.BigInteger.ONE.shiftLeft(160).negate().add(java.math.BigInteger.ONE);
        List<Object> tail = new ArrayList<>(List.of("tail"));
        return new ArrayList<>(List.of(big, tail, big, tail));
    }

    static List<Object> review2_bigint_boundaries() {
        List<Object> values = new ArrayList<>();
        for (int bits : new int[] {31, 32, 63, 64, 65, 95, 96, 127, 128, 1000}) {
            java.math.BigInteger power = java.math.BigInteger.ONE.shiftLeft(bits);
            values.add(power.subtract(java.math.BigInteger.ONE));
            values.add(power);
            values.add(power.negate());
        }
        values.add(new java.math.BigInteger(1, new byte[] {0, 0, (byte) 0x80, 0, 0, 0, 0, 0, 0, 0, 1}));
        values.add(java.math.BigInteger.ZERO);
        return values;
    }

    static List<Object> review2_boxed_boundaries() {
        return new ArrayList<>(List.of(Byte.MIN_VALUE, Byte.MAX_VALUE, Short.MIN_VALUE, Short.MAX_VALUE,
            0.1f, Float.MIN_VALUE, Float.MIN_NORMAL, Float.MAX_VALUE, Float.NaN,
            Float.POSITIVE_INFINITY, Float.NEGATIVE_INFINITY));
    }

    static List<Object> review2_boxed_refs() {
        Object value = Float.valueOf(1.25f);
        List<Object> tail = new ArrayList<>(List.of("tail"));
        return new ArrayList<>(List.of(value, value, tail, tail));
    }

    static List<Object> review2_time_boundaries() {
        return new ArrayList<>(List.of(java.time.LocalDate.of(1, 1, 1), java.time.LocalDate.of(9999, 12, 31),
            java.time.LocalTime.MIDNIGHT, java.time.LocalTime.of(23, 59, 59, 999999000),
            java.time.Duration.ofNanos(-1), java.time.Duration.ofSeconds(-86400, 1000),
            java.time.Duration.ofSeconds(86399999999999L, 999999000)));
    }

    static List<Object> review2_time_overlap() {
        java.time.LocalDateTime local = java.time.LocalDateTime.of(2024, 11, 3, 1, 30);
        java.time.ZoneId zone = java.time.ZoneId.of("America/New_York");
        return new ArrayList<>(List.of(
            java.time.ZonedDateTime.ofLocal(local, zone, java.time.ZoneOffset.ofHours(-4)),
            java.time.ZonedDateTime.ofLocal(local, zone, java.time.ZoneOffset.ofHours(-5))));
    }

    static java.time.ZonedDateTime review2_zoned_min() {
        return java.time.ZonedDateTime.of(1, 1, 1, 0, 0, 0, 0, java.time.ZoneId.of("Etc/GMT-1"));
    }

    static java.time.ZonedDateTime review2_zoned_max() {
        return java.time.ZonedDateTime.of(9999, 12, 31, 23, 59, 59, 0, java.time.ZoneId.of("Etc/GMT+1"));
    }
}
