What the `18.0` branch carries beyond `main`: one file, the tour state's
import path (`pan_mail_pro/static/src/compat/tour.js`, which 18 keeps under
`@web_tour/tour_service/tour_state`). Everything else 18 needs is in the seam
on `main` (`pan_mail_pro/compat.py`, `static/src/compat/`). Applied by
`tools/cut_series.sh 18.0`.
