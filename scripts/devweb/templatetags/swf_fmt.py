"""Dev stand-ins for monitor_app's swf_fmt filters (subset used by
canary templates). Signatures and semantics match the platform's."""
from datetime import datetime

from django import template
from zoneinfo import ZoneInfo

register = template.Library()

EASTERN = ZoneInfo('America/New_York')


@register.filter(name='fmt_dt')
def fmt_dt(value):
    """YYYYMMDD HH:MM:SS in Eastern; '' for falsy; original on parse fail."""
    if not value:
        return ''
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    try:
        return value.astimezone(EASTERN).strftime('%Y%m%d %H:%M:%S')
    except (ValueError, AttributeError):
        return str(value)


@register.filter(name='state_class')
def state_class(value):
    if not value:
        return ''
    return f'{str(value).lower()}_fill'


@register.filter(name='state_title')
def state_title(value):
    return ''
