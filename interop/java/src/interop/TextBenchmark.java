package interop;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;

/** Measures complete Hessian calls on the same strings and input bytes as Python. */
public final class TextBenchmark {
    static volatile Object sink;

    interface Call { Object run() throws Exception; }

    static double measure(Call call, int iterations, int rounds) throws Exception {
        for (int i = 0; i < 100; i++) sink = call.run();
        double[] times = new double[rounds];
        for (int round = 0; round < rounds; round++) {
            long start = System.nanoTime();
            for (int i = 0; i < iterations; i++) sink = call.run();
            times[round] = (System.nanoTime() - start) / (1000.0 * iterations);
        }
        Arrays.sort(times);
        return (times[(rounds - 1) / 2] + times[rounds / 2]) / 2;
    }

    public static void main(String[] args) throws Exception {
        Path directory = Path.of(args[0]);
        int iterations = Integer.parseInt(args[1]), rounds = Integer.parseInt(args[2]);
        StringBuilder json = new StringBuilder("{");
        for (String name : List.of("ascii", "chinese", "mixed", "emoji")) {
            String text = Files.readString(directory.resolve(name + ".txt"));
            byte[] wire = Files.readAllBytes(directory.resolve(name + ".bin"));
            if (!Interop.readCaucho(wire).equals(List.of(text)) || !Interop.readLite(wire).equals(List.of(text))) {
                throw new AssertionError("decoded text differs: " + name);
            }
            // Writers must produce the same chunk boundaries before comparing costs.
            if (!Arrays.equals(Interop.caucho(List.of(text)), wire) || !Arrays.equals(Interop.lite(List.of(text)), wire)) {
                throw new AssertionError("Java bytes differ: " + name);
            }
            if (json.length() > 1) json.append(',');
            json.append('"').append(name).append("\":{");
            json.append("\"caucho\":{\"dumps\":").append(measure(() -> Interop.caucho(List.of(text)), iterations, rounds));
            json.append(",\"loads\":").append(measure(() -> Interop.readCaucho(wire), iterations, rounds)).append("},");
            json.append("\"lite\":{\"dumps\":").append(measure(() -> Interop.lite(List.of(text)), iterations, rounds));
            json.append(",\"loads\":").append(measure(() -> Interop.readLite(wire), iterations, rounds)).append("}}");
        }
        System.out.println(json.append('}'));
    }
}
