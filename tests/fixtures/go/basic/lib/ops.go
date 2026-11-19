package differentlyNamed

func Export() int { return private() }
func private() int { return 2 }
