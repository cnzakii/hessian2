// Command textbench compares complete string calls using shared Hessian input bytes.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"time"

	hessian "github.com/apache/dubbo-go-hessian2"
)

var sink interface{}

func measure(call func() (interface{}, error), iterations, rounds int) (float64, error) {
	for i := 0; i < 100; i++ {
		value, err := call()
		if err != nil {
			return 0, err
		}
		sink = value
	}
	times := make([]float64, rounds)
	for round := range times {
		start := time.Now()
		for i := 0; i < iterations; i++ {
			value, err := call()
			if err != nil {
				return 0, err
			}
			sink = value
		}
		times[round] = float64(time.Since(start).Nanoseconds()) / (1000 * float64(iterations))
	}
	sort.Float64s(times)
	return (times[(rounds-1)/2] + times[rounds/2]) / 2, nil
}

func run() error {
	iterations, err := strconv.Atoi(os.Args[2])
	if err != nil {
		return err
	}
	rounds, err := strconv.Atoi(os.Args[3])
	if err != nil {
		return err
	}
	results := map[string]map[string]float64{}
	for _, name := range []string{"ascii", "chinese", "mixed", "emoji"} {
		raw, err := os.ReadFile(filepath.Join(os.Args[1], name+".txt"))
		if err != nil {
			return err
		}
		text := string(raw)
		wire, err := os.ReadFile(filepath.Join(os.Args[1], name+".bin"))
		if err != nil {
			return err
		}
		encode := func() (interface{}, error) {
			encoder := hessian.NewEncoder()
			if err := encoder.Encode(text); err != nil {
				return nil, err
			}
			return encoder.Buffer(), nil
		}
		decode := func(data []byte) (interface{}, error) { return hessian.NewDecoder(data).Decode() }
		value, err := decode(wire)
		if err != nil {
			return err
		}
		if value != text {
			return fmt.Errorf("decoded shared text differs: %s", name)
		}
		own, err := encode()
		if err != nil {
			return err
		}
		value, err = decode(own.([]byte))
		if err != nil {
			return err
		}
		if value != text {
			return fmt.Errorf("decoded own text differs: %s", name)
		}
		dumps, err := measure(encode, iterations, rounds)
		if err != nil {
			return err
		}
		loads, err := measure(func() (interface{}, error) { return decode(wire) }, iterations, rounds)
		if err != nil {
			return err
		}
		ownLoads, err := measure(func() (interface{}, error) { return decode(own.([]byte)) }, iterations, rounds)
		if err != nil {
			return err
		}
		results[name] = map[string]float64{"dumps": dumps, "loads": loads, "loads_own_chunks": ownLoads}
	}
	return json.NewEncoder(os.Stdout).Encode(results)
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
