import 'package:demo/ops.dart' as api;

int drive() {
  final callback = api.leaf;
  return api.leaf(1) + callback(2);
}

void main() { print(drive()); }
