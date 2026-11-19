private func leaf(_ value: Int) -> Int { return value + 1 }
let boot = leaf(0)

private struct Repo {
    static func save(_ value: Int) -> Int { return leaf(value) }
}

func drive() -> Int {
    let callback = leaf(_:)
    return leaf(1) + Repo.save(2) + callback(1)
}

print(drive())
