<?php namespace Example;use Example\Types\{Resolver};class Service implements Resolver{use Concrete;public function run():string{return $this->getContainer();}}
