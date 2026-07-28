"""Render a structured coach report to self-contained HTML and best-effort PDF."""
import html as _html
import re as _re

_CSS = """
body{font-family:'Noto Sans CJK SC','Microsoft YaHei','PingFang TC','Heiti TC',
     'SimHei',sans-serif;color:#1a1a1a;max-width:820px;margin:24px auto;padding:0 16px;line-height:1.5}
h1{font-size:1.5rem;margin:0 0 4px} h2{font-size:1.1rem;border-bottom:2px solid #ddd;padding-bottom:4px;margin-top:24px}
.meta{color:#666;font-size:.85rem} .verdict{background:#f4f6f8;border-radius:8px;padding:12px;margin:12px 0}
table{border-collapse:collapse;width:100%;font-size:.9rem} th,td{border:1px solid #ddd;padding:6px 8px;text-align:left}
.finding{border-left:4px solid #c0392b;background:#fdf3f2;border-radius:0 6px 6px 0;padding:10px;margin:8px 0}
.strength{border-left:4px solid #27ae60;background:#f0f9f4;border-radius:0 6px 6px 0;padding:10px;margin:8px 0}
.impact{display:inline-block;background:#333;color:#fff;border-radius:4px;padding:1px 8px;font-size:.75rem}
"""


def _esc(v):
    return _html.escape(str(v), quote=True) if v is not None else ""


def render_html(report):
    h = report.get("header", {})
    s = report.get("summary", {})
    # Section labels are threaded in from the builder (already localised).
    # English fallbacks keep the renderer safe when section_labels is absent.
    labels = report.get("section_labels", {})
    lbl_strengths    = labels.get("strengths",      "Strengths")
    lbl_weaknesses   = labels.get("weaknesses",     "Areas to Improve")
    lbl_per_rep      = labels.get("per_rep",        "Per-Rep Breakdown")
    lbl_training     = labels.get("training_plan",  "Training Plan")
    lbl_col_rep      = labels.get("col_rep",        "Rep")
    lbl_col_score    = labels.get("col_score",      "Score")
    lbl_col_weakness = labels.get("col_top_weakness", "Top Weakness")

    parts = []
    parts.append("<!doctype html><html lang='" + _esc(report.get("lang", "en")) + "'><head>"
                 "<meta charset='utf-8'><style>" + _CSS + "</style></head><body>")
    parts.append("<h1>" + _esc(h.get("stroke_label")) + "</h1>")
    angle_space = "3D" if h.get("feature_space") == "3d" else "2D"
    parts.append("<div class='meta'>" + _esc(h.get("date")) + " · " + _esc(h.get("rep_count"))
                 + " reps · " + _esc(h.get("dominant_hand")) + " · " + _esc(h.get("pose_family"))
                 + " · " + angle_space + " angles</div>")

    # Primary score = the final score (biomechanical heuristic). Falls back to
    # mean_score for older reports built before mean_final_score existed.
    final_mean = s.get("mean_final_score", s.get("mean_score"))
    parts.append("<div class='verdict'><b>"
                 + ("Final score " + _esc(final_mean) if final_mean is not None else "")
                 + (" · Consistency " + _esc(s.get("consistency")) if s.get("consistency") is not None else "")
                 + "</b><br>" + _esc(s.get("verdict_text")) + "</div>")

    strengths = report.get("strengths", [])
    if strengths:
        parts.append("<h2>" + _esc(lbl_strengths) + "</h2>")
        for st in strengths:
            parts.append("<div class='strength'><b>" + _esc(st.get("metric_label"))
                         + "</b> <span class='impact'>" + _esc(st.get("impact_label")) + "</span><br>"
                         + _esc(st.get("text")) + "</div>")

    weaknesses = report.get("weaknesses", [])
    if weaknesses:
        parts.append("<h2>" + _esc(lbl_weaknesses) + "</h2>")
        for w in weaknesses:
            _ideal = list(w.get("ideal_range") or [])
            ideal_lo = _ideal[0] if len(_ideal) > 0 else ""
            ideal_hi = _ideal[1] if len(_ideal) > 1 else ""
            _shadow = w.get("measured_shadow_2d")
            _shadow_txt = (" (2D: " + _esc(_shadow) + ")") if _shadow is not None else ""
            parts.append("<div class='finding'><b>" + _esc(w.get("metric_label"))
                         + "</b> <span class='impact'>" + _esc(w.get("impact_label")) + "</span><br>"
                         + "measured " + _esc(w.get("measured")) + _shadow_txt + " (ideal "
                         + _esc(ideal_lo) + "–" + _esc(ideal_hi) + ")<br>"
                         + _esc(w.get("mechanism_text")) + "<br><i>" + _esc(w.get("drill_text")) + "</i></div>")

    per_rep = report.get("per_rep", [])
    if per_rep:
        parts.append("<h2>" + _esc(lbl_per_rep) + "</h2>"
                     + "<table><tr><th>" + _esc(lbl_col_rep) + "</th><th>" + _esc(lbl_col_score)
                     + "</th><th>" + _esc(lbl_col_weakness) + "</th></tr>")
        for r in per_rep:
            # Final score (heuristic) is the score of record; fall back to overall_score
            # for older report dicts built before final_score existed.
            final = r.get("final_score")
            if final is None:
                final = r.get("overall_score")
            parts.append("<tr><td>" + _esc(r.get("rep_id")) + "</td><td>" + _esc(final)
                         + "</td><td>" + _esc(r.get("top_weakness")) + "</td></tr>")
        parts.append("</table>")

    plan = report.get("training_plan", {})
    weeks = plan.get("weeks", []) if isinstance(plan, dict) else []
    if weeks:
        parts.append("<h2>" + _esc(lbl_training) + "</h2>")
        for wk in weeks:
            parts.append("<b>Week " + _esc(wk.get("week")) + " (" + _esc(wk.get("phase")) + ")</b><ul>")
            for sess in wk.get("sessions", []):
                parts.append("<li>" + _esc(sess.get("name")) + " — " + _esc(sess.get("sets"))
                             + "x" + _esc(sess.get("reps")) + ", " + _esc(sess.get("frequency_per_week"))
                             + "/wk</li>")
            parts.append("</ul>")

    parts.append("</body></html>")
    return "".join(parts)


def render_pdf(html, out_path):
    try:
        import weasyprint
    except Exception:
        return _render_pdf_xhtml2pdf(html, out_path)
    try:
        weasyprint.HTML(string=html).write_pdf(out_path)
        return True
    except Exception:
        return _render_pdf_xhtml2pdf(html, out_path)


# reportlab's built-in CID fonts (Adobe-GB1/CNS1 collections) render CJK glyphs
# without needing an embedded/bundled font file -- most PDF viewers substitute
# a local CJK font for these standard, non-embedded font references. xhtml2pdf
# only auto-registers one of these when a CSS font-family value matches its
# name exactly (see xhtml2pdf.context.Context.getFontName), so plain font
# stacks like "Microsoft YaHei, SimHei, sans-serif" resolve to Helvetica (no
# CJK glyphs, renders as tofu boxes) unless we force one of these names in.
_CJK_CID_FONT_BY_LANG = {"zh-hans": "STSong-Light", "zh-hant": "MSung-Light"}


def _render_pdf_xhtml2pdf(html, out_path):
    """Pure-Python fallback for machines without weasyprint's native GTK deps."""
    try:
        from xhtml2pdf import pisa
    except Exception:
        return False
    lang_match = _re.search(r"<html[^>]*\blang=['\"]([^'\"]+)['\"]", html, _re.IGNORECASE)
    lang = lang_match.group(1).lower() if lang_match else ""
    cid_font = _CJK_CID_FONT_BY_LANG.get(lang)
    if cid_font:
        override = "<style>*{font-family:'" + cid_font + "' !important}</style></head>"
        html = html.replace("</head>", override, 1)
    try:
        with open(out_path, "wb") as f:
            result = pisa.CreatePDF(html, dest=f)
        return not result.err
    except Exception:
        return False
