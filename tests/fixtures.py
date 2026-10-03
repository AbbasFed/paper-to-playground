"""A small generic candidate (no paper content) with size-linked vector and matrix controls."""
import json

EXCERPT = "This is a generic test page for resizing vectors and matrices. Section 1 defines Eq. (1): y = sum of p."

CONTENT = {
    "title": "Resize test", "paper": "Generic test", "section": "Section 1", "equation": "Eq. (1)", "formula": "y = Σ p<sub>i</sub>",
    "intro": "A generic page for resizing vectors and matrices.", "why": "It exercises size sliders.",
    "symbols": [{"symbol": "p", "meaning": "weights"}, {"symbol": "M", "meaning": "a matrix"}],
    "steps": ["Pick the sizes.", "Sum the entries in use."],
    "explorations": [
        {"title": "Shrink", "change": "Set n to 1 and r to 1", "observe": "Only one entry remains", "why": "Size sliders cut the inputs", "set": {"n": 1, "r": 1},
         "expect": "out.count === 1"},
        {"title": "Grow", "change": "Set n to 5", "observe": "Five bars appear", "why": "More entries are passed on", "set": {"n": 2}, "then": {"n": 5},
         "expect": "out2.count === 5 && out2.total > out.total"}],
    "limitation": "Toy sums only.", "quotes": ["a generic test page for resizing"], "simplifications": ["Made-up numbers."]}

CONTROLS = [
    {"type": "slider", "id": "n", "label": "entries n", "min": 1, "max": 5, "step": 1, "default": 3},
    {"type": "vector", "id": "p", "label": "weights p", "default": [1, 2, 3, 4, 5], "min": 0, "max": 9, "step": 1, "lengthFrom": "n"},
    {"type": "slider", "id": "r", "label": "rows r", "min": 1, "max": 3, "step": 1, "default": 2},
    {"type": "slider", "id": "k", "label": "columns k", "min": 1, "max": 3, "step": 1, "default": 2},
    {"type": "matrix", "id": "M", "label": "matrix M", "default": [[1, 2, 3], [4, 5, 6], [7, 8, 9]], "min": -9, "max": 9, "step": 1,
     "rowsFrom": "r", "colsFrom": "k"}]

COMPUTE = """function compute(p) {
  var rs = p.M.map(function (row) { return H.sum(row); });
  return { total: H.sum(p.p), count: p.p.length, rowSums: rs, rows: p.M.length, cols: p.M[0].length, grand: H.sum(rs) };
}"""

RENDER = """function render(p, out) {
  return H.grid(2, [H.bars({ labels: p.p.map(function (_, i) { return 'p' + (i + 1); }), values: p.p, title: 'p' }),
                    H.heatmap({ matrix: p.M, title: 'M', digits: 0 })]);
}"""

READOUTS = [{"key": "total", "label": "total"}, {"key": "count", "label": "entries used"}, {"key": "rowSums", "label": "row sums"},
            {"key": "grand", "label": "grand total"}]

TESTS = [{"name": "count matches n", "params": {}, "expect": "out.count === p.n"},
         {"name": "shape matches r and k", "params": {}, "expect": "out.rows === p.r && out.cols === p.k"},
         {"name": "known case", "params": {"n": 2}, "expect": "out.total === 3"}]


def tags(**over):
    """Raw tag texts as the model would send them; keyword arguments replace parts."""
    parts = {"content": CONTENT, "controls": CONTROLS, "compute": COMPUTE, "render": RENDER, "readouts": READOUTS, "tests": TESTS}
    parts.update(over)
    return {k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) for k, v in parts.items()}
