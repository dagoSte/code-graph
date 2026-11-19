<?php
require __DIR__ . '/callbacks.php';
function register_shortcodes() {
    add_shortcode('tag_only', 'render_shortcode');
}
register_shortcodes();
$shortcode = new Shortcode();
$shortcode->register();
