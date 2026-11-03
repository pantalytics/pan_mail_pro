/** @odoo-module */
// Odoo's tour state, which every series keeps at a path of its own:
// `@web_tour/js/tour_state` on 19, `@web_tour/tour_service/tour_state` on
// 18 and `@web_tour/tour_state` on 20 (tools/series/<series> carries the
// other two). The object is the same: `getCurrentTour()` is what the Inbox
// asks. The tour service itself is `tour_service` on all three.
export { tourState } from "@web_tour/js/tour_state";
