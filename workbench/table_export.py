"""Safe table exports from persisted model structure, never from client-supplied HTML."""

import csv
import html
import io


def table_html(table, *, row_limit=None):
    """Preserve header cells and spans; escape every piece of document content."""
    cells = {(c["row"], c["column"]): c for c in table["cells"]}
    covered = set()
    output = ["<table><caption>" + html.escape(table["title"] or table["id"]) + "</caption>"]
    for row in range(min(table["row_count"], row_limit or 300)):
        output.append("<tr>")
        for column in range(table["column_count"]):
            if (row, column) in covered:
                continue
            cell = cells.get((row, column))
            if cell is None:
                output.append("<td></td>")
                continue
            tag = "th" if cell["is_header"] else "td"
            output.append(
                f'<{tag} rowspan="{cell["row_span"]}" colspan="{cell["column_span"]}">'
                + html.escape(cell["text"])
                + f"</{tag}>"
            )
            for r in range(row, row + cell["row_span"]):
                for c in range(column, column + cell["column_span"]):
                    covered.add((r, c))
        output.append("</tr>")
    output.append("</table>")
    return "".join(output)


def table_csv(table):
    """Merged text is emitted once at its anchor; covered cells stay empty.

    Prefix spreadsheet formula-looking values with an apostrophe. The untouched
    transcription remains available in JSON and escaped HTML exports.
    """
    cells = {(c["row"], c["column"]): c["text"] for c in table["cells"]}
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    for row in range(table["row_count"]):
        values = []
        for column in range(table["column_count"]):
            value = cells.get((row, column), "")
            if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
                value = "'" + value
            values.append(value)
        writer.writerow(values)
    return "\ufeff" + out.getvalue()
