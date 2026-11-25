"""Tiny declarative form helper: a list of fields drives both rendering
(templates/_form_fields.html) and parsing of the submitted values."""
from datetime import datetime
from decimal import Decimal, InvalidOperation


class Field:
    def __init__(self, name, label, type="text", required=False, choices=None, help=None, wide=False):
        self.name = name
        self.label = label
        self.type = type  # text, textarea, date, decimal, int, select, fk, email, url, checkbox
        self.required = required
        self._choices = choices
        self.help = help
        self.wide = wide or type == "textarea"

    def choices(self, ctx=None):
        c = self._choices
        return c(ctx) if callable(c) else (c or [])

    def display_value(self, obj, submitted=None):
        if submitted is not None and (self.name in submitted or self.type == "checkbox"):
            if self.type == "checkbox":
                return self.name in submitted
            return submitted.get(self.name, "")
        val = getattr(obj, self.name, None) if obj is not None else None
        if val is None:
            return "" if self.type != "checkbox" else False
        if hasattr(val, "isoformat"):
            return val.isoformat()
        if self.type == "decimal":
            return format(val, "f")
        return str(val) if self.type != "checkbox" else bool(val)


def parse_field(field, form, ctx=None):
    if field.type == "checkbox":
        return field.name in form
    raw = (form.get(field.name) or "").strip()
    if raw == "":
        if field.required:
            raise ValueError(f"{field.label} is required.")
        return None
    try:
        if field.type == "date":
            return datetime.strptime(raw, "%Y-%m-%d").date()
        if field.type == "decimal":
            return Decimal(raw.replace(" ", "").replace(",", "."))
        if field.type == "int":
            return int(raw)
    except (ValueError, InvalidOperation):
        raise ValueError(f"{field.label}: '{raw}' is not valid.")
    if field.type in ("select", "fk"):
        allowed = {str(v) for v, _ in field.choices(ctx)}
        if raw not in allowed:
            raise ValueError(f"{field.label}: invalid choice.")
        return int(raw) if field.type == "fk" else raw
    return raw


def apply_form(obj, fields, form, ctx=None):
    """Validate all fields; only assign to ``obj`` when everything is valid."""
    errors, values = [], {}
    for f in fields:
        try:
            values[f.name] = parse_field(f, form, ctx)
        except ValueError as e:
            errors.append(str(e))
    if not errors:
        for k, v in values.items():
            setattr(obj, k, v)
    return errors
