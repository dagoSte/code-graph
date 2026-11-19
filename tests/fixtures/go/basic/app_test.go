package app

import "testing"

func TestRun(t *testing.T) {
    var r Repo
    if Run(&r) != 3 || r.Count != 1 { t.Fatal("wrong call flow") }
    if Typed() != 2 || Generic() != 1 { t.Fatal("wrong receiver or generic result") }
}
