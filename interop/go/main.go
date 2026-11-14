// Command interop produces and checks Hessian bytes with dubbo-go-hessian2.
//
//	write OUT.json   every case as Go's bytes and a structural description
//	describe DIR     decodes each DIR/NAME.bin and prints
//	                 {NAME: {"values": [description]} or {"error": message}}
//	types DIR        decodes each DIR/NAME.bin and prints {NAME: Go type},
//	                 such as "[]hessian.Object", or {NAME: {"error": message}}
//
// Descriptions match interop/java and the Python tests' describe.py, except
// that maps compare without order and references are not tracked.
package main

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"time"
	"unicode/utf16"

	hessian "github.com/apache/dubbo-go-hessian2"
)

// User is registered under a Java class name, as Go applications do.
type User struct {
	Name  string
	Age   int32
	ID    int64
	Score float64
	Tags  []interface{}
}

func (User) JavaClassName() string { return "com.example.User" }

func cases() map[string]interface{} {
	return map[string]interface{}{
		"null":           nil,
		"true":           true,
		"int_0":          int32(0),
		"int_m16":        int32(-16),
		"int_0x7ff":      int32(0x7ff),
		"int_0x3ffff":    int32(0x3ffff),
		"int_max":        int32(math.MaxInt32),
		"int_min":        int32(math.MinInt32),
		"long_0":         int64(0),
		"long_max":       int64(math.MaxInt64),
		"long_min":       int64(math.MinInt64),
		"long_2_31":      int64(1) << 31,
		"double_0":       0.0,
		"double_m128":    -128.0,
		"double_32767":   32767.0,
		"double_0_001":   0.001,
		"double_3_14159": 3.14159,
		"double_inf":     math.Inf(1),
		// 0.001 * 9 in binary64: Java writes it as 0x5f 9, which Go reads as 9 / 1000.
		"double_mill_ulp": math.Float64frombits(0x3f826e978d4fdf3c),
		"string_empty":    "",
		"string_ascii":    "hello",
		"string_cjk":      "中文测试",
		"string_emoji":    "a😀b",
		"string_chunk":    strings.Repeat("a", 4095) + "😀" + strings.Repeat("b", 5000),
		"string_long":     strings.Repeat("😀", 20000),
		"binary_0":        []byte{},
		"binary_1024":     make([]byte, 1024),
		"binary_70000":    make([]byte, 70000),
		"date_millis":     time.UnixMilli(1_700_000_000_123).UTC(),
		"date_minute":     time.UnixMilli(1_700_000_040_000).UTC(),
		"list":            []interface{}{int32(1), "two", 3.5, nil},
		"list_nested":     []interface{}{[]interface{}{int32(1)}, []interface{}{}},
		// One entry: Go encodes maps in random order.
		"map": map[interface{}]interface{}{int32(2): "b"},
		"object_nested": &User{Name: "parent", Tags: []interface{}{
			&User{Name: "child", Tags: []interface{}{nil}},
			map[interface{}]interface{}{"leaf": &User{Name: "mapped"}},
		}},
		"wrapper_nulls": []interface{}{nil, int32(-1), nil, int64(1) << 40, true},
		"matrix":        []interface{}{[]interface{}{}, []interface{}{int32(-1), int32(2)}},
		"object":        &User{Name: "ann", Age: 30, ID: 1 << 40, Score: 98.5, Tags: []interface{}{"x"}},
	}
}

func main() {
	hessian.RegisterPOJO(&User{})
	var err error
	switch os.Args[1] {
	case "write":
		err = write(os.Args[2])
	case "describe":
		err = describeDir(os.Args[2])
	case "types":
		err = typesDir(os.Args[2])
	default:
		err = fmt.Errorf("unknown command %q", os.Args[1])
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func write(out string) error {
	result := map[string]interface{}{}
	for name, value := range cases() {
		encoder := hessian.NewEncoder()
		if err := encoder.Encode(value); err != nil {
			return fmt.Errorf("%s: %w", name, err)
		}
		result[name] = map[string]interface{}{
			"values": []interface{}{describe(value)},
			"go":     base64.StdEncoding.EncodeToString(encoder.Buffer()),
		}
	}
	data, err := json.MarshalIndent(result, "", " ")
	if err != nil {
		return err
	}
	return os.WriteFile(out, append(data, '\n'), 0o644)
}

// typesDir prints the Go type each value decodes as, which Go code that
// type-asserts decoded values depends on.
func typesDir(dir string) error {
	files, err := filepath.Glob(filepath.Join(dir, "*.bin"))
	if err != nil {
		return err
	}
	result := map[string]interface{}{}
	for _, file := range files {
		name := strings.TrimSuffix(filepath.Base(file), ".bin")
		data, err := os.ReadFile(file)
		if err != nil {
			return err
		}
		value, err := hessian.NewDecoder(data).Decode()
		if err != nil {
			result[name] = map[string]interface{}{"error": err.Error()}
			continue
		}
		result[name] = reflect.TypeOf(value).String()
	}
	data, err := json.Marshal(result)
	if err != nil {
		return err
	}
	fmt.Println(string(data))
	return nil
}

func describeDir(dir string) error {
	files, err := filepath.Glob(filepath.Join(dir, "*.bin"))
	if err != nil {
		return err
	}
	result := map[string]interface{}{}
	for _, file := range files {
		name := strings.TrimSuffix(filepath.Base(file), ".bin")
		data, err := os.ReadFile(file)
		if err != nil {
			return err
		}
		value, err := hessian.NewDecoder(data).Decode()
		if err != nil {
			result[name] = map[string]interface{}{"error": err.Error()}
			continue
		}
		result[name] = map[string]interface{}{"values": []interface{}{describe(value)}}
	}
	data, err := json.Marshal(result)
	if err != nil {
		return err
	}
	fmt.Println(string(data))
	return nil
}

const large = 1024

func describe(value interface{}) interface{} {
	switch v := value.(type) {
	case nil:
		return nil
	case bool:
		return map[string]interface{}{"bool": v}
	case int32, int64, int:
		return map[string]interface{}{"int": fmt.Sprint(v)}
	case float64:
		return map[string]interface{}{"double": fmt.Sprintf("%x", math.Float64bits(v))}
	case string:
		units := utf16.Encode([]rune(v))
		if len(units) > large {
			raw := make([]byte, 2*len(units))
			for i, unit := range units {
				raw[2*i], raw[2*i+1] = byte(unit), byte(unit>>8)
			}
			sum := sha256.Sum256(raw)
			return map[string]interface{}{"str_sha256": hex.EncodeToString(sum[:])}
		}
		return map[string]interface{}{"str": v}
	case []byte:
		if len(v) > large {
			sum := sha256.Sum256(v)
			return map[string]interface{}{"bin_sha256": hex.EncodeToString(sum[:])}
		}
		return map[string]interface{}{"bin": base64.StdEncoding.EncodeToString(v)}
	case time.Time:
		return map[string]interface{}{"date": fmt.Sprint(v.UnixMilli())}
	case []interface{}:
		items := make([]interface{}, len(v))
		for i, item := range v {
			items[i] = describe(item)
		}
		return map[string]interface{}{"list": items}
	case map[interface{}]interface{}:
		pairs := make([]interface{}, 0, len(v))
		for key, item := range v {
			pairs = append(pairs, []interface{}{describe(key), describe(item)})
		}
		sort.Slice(pairs, func(i, j int) bool { return fmt.Sprint(pairs[i]) < fmt.Sprint(pairs[j]) })
		return map[string]interface{}{"map": pairs}
	case *User:
		return describe(*v)
	case User:
		return map[string]interface{}{"object": v.JavaClassName(), "fields": map[string]interface{}{
			"name": describe(v.Name), "age": describe(v.Age), "iD": describe(v.ID),
			"score": describe(v.Score), "tags": describe(v.Tags),
		}}
	}
	return map[string]interface{}{"unsupported": reflect.TypeOf(value).String()}
}
