<?php
function render_shortcode($attributes = [], $content = null, $tag = '') {
    return 'function';
}
function tag_only() {
    return 'data';
}
class Shortcode {
    public function render($attributes = [], $content = null, $tag = '') {
        return 'method';
    }
    public function register() {
        add_shortcode('method', [$this, 'render']);
    }
}
class UnrelatedShortcode {
    public function render() {
        return 'unrelated';
    }
}
