<?php
class Svc {
    private function fmt($x) {
        return $x;
    }
    public function all($xs) {
        return array_map([$this, 'fmt'], $xs);
    }
}
