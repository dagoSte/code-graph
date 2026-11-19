package app

import alias "example.com/fixture/lib"

func Run(r *Repo) int {
    r.Save(1)
    defer after()
    return helper() + alias.Export()
}

func helper() int { return 1 }
func after() {}
func init() {}
func Identity[T any](v T) T { return v }
func Generic() int { return Identity[int](1) }
