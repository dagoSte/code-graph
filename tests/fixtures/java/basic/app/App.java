package app;
import lib.Ops;
import static lib.Ops.extra;

public final class App {
    static int seed = Ops.leaf(1);
    private int hidden() { return Ops.leaf(1); }
    int drive() { return hidden() + extra() + Local.leaf(); }
    static java.util.function.IntUnaryOperator callback() { return Ops::leaf; }
    public static void main(String[] args) { System.out.println(new App().drive()); }
}
