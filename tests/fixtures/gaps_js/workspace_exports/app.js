import { visible } from "@demo/lib";
import { polish } from "@demo/lib/private";
import { shine } from "@demo/lib/tools";
import { opened } from "@demo/open";
import { deep } from "@demo/open/src/deep";
export function drive() { return visible() + polish() + shine() + opened() + deep(); }
