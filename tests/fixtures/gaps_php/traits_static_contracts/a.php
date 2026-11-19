<?php
namespace App;
interface I { public function m(); }
abstract class Base { abstract public function n(); public function run() { return $this->n(); } }
class A extends Base implements I { public function m() { return 1; } public function n() { return 2; } }
function useit(I $x) { return $x->m(); }
function direct() { $a = new A(); return $a->m(); }
