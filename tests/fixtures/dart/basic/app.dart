import 'lib/ops.dart' as ops show polish;

int leaf(int value) => value + 1;
final seed = leaf(1);

class Repo {
  static int save(int value) => value + 2;
  int run(int value) => value;
}

int drive(int value) {
  final callback = ops.polish;
  return leaf(value) + ops.polish(value) + Repo.save(value) + callback(value);
}

int indirect(Repo repo) => repo.run(1);
void main() { print(drive(1)); }
