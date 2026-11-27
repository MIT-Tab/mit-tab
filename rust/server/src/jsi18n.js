(function (global) {
  const django = global.django || (global.django = {});
  django.gettext = (message) => message;
  django.gettext_noop = django.gettext;
  django.pluralidx = (count) => (count === 1 ? 0 : 1);
  django.ngettext = (singular, plural, count) => (django.pluralidx(count) === 0 ? singular : plural);
  django.pgettext = (_context, message) => message;
  django.npgettext = (_context, singular, plural, count) => django.ngettext(singular, plural, count);
  django.interpolate = (format, values, named) => {
    let index = 0;
    return named
      ? format.replace(/%\(([^)]+)\)s/g, (_match, key) => String(values[key]))
      : format.replace(/%s/g, () => String(values[index++]));
  };
  const formats = {
    DATE_FORMAT: "N j, Y",
    DATETIME_FORMAT: "N j, Y, P",
    TIME_FORMAT: "P",
    FIRST_DAY_OF_WEEK: 0,
    DECIMAL_SEPARATOR: ".",
    THOUSAND_SEPARATOR: ",",
    NUMBER_GROUPING: 3,
  };
  django.get_format = (name) => formats[name] ?? name;
  for (const api of ["gettext", "gettext_noop", "pluralidx", "ngettext", "pgettext",
    "npgettext", "interpolate", "get_format"]) {
    global[api] = django[api];
  }
}(window));
