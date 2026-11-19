package app

type Repo struct { Count int }
type Runner interface { Save(int) }

func (r *Repo) Save(n int) { r.Count += n }
func (r Repo) Value() int { return r.Count }
func Indirect(r Runner) { r.Save(1) }
func Typed() int { var r Repo; r.Save(2); return r.Value() }
