import '../css/spreadsheet.scss';

const VENDOR_ASSETS = {
  css: [
    [
      'https://cdn.jsdelivr.net/npm/jsuites@4.9.29/dist/jsuites.min.css',
      '/static/vendor/jsuites.min.css',
    ],
    [
      'https://cdn.jsdelivr.net/npm/jspreadsheet-ce@4.10.1/dist/jspreadsheet.min.css',
      '/static/vendor/jspreadsheet.min.css',
    ],
  ],
  js: [
    [
      'https://cdn.jsdelivr.net/npm/jsuites@4.9.29/dist/jsuites.min.js',
      '/static/vendor/jsuites.min.js',
    ],
    [
      'https://cdn.jsdelivr.net/npm/jspreadsheet-ce@4.10.1/dist/index.min.js',
      '/static/vendor/jspreadsheet.min.js',
    ],
  ],
};

const loadStylesheet = (href) => new Promise((resolve, reject) => {
  if (document.querySelector(`link[data-vendor-source="${href}"]`)) {
    resolve();
    return;
  }

  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = href;
  link.dataset.vendorSource = href;
  if (href.startsWith('https://')) {
    link.crossOrigin = 'anonymous';
  }
  link.onload = () => resolve();
  link.onerror = () => reject(new Error(`Failed to load stylesheet ${href}`));
  document.head.appendChild(link);
});

const loadScript = (src) => new Promise((resolve, reject) => {
  if (document.querySelector(`script[data-vendor-source="${src}"]`)) {
    if (typeof window.jspreadsheet !== 'undefined' || typeof window.jSpreadsheet !== 'undefined') {
      resolve();
    } else {
      window.addEventListener('load', resolve, { once: true });
    }
    return;
  }

  const script = document.createElement('script');
  script.src = src;
  script.defer = true;
  script.dataset.vendorSource = src;
  script.onload = () => resolve();
  script.onerror = () => reject(new Error(`Failed to load script ${src}`));
  document.head.appendChild(script);
});

const loadSequentially = (loader, sources) => new Promise((resolve, reject) => {
  const attempt = (index) => {
    if (index >= sources.length) {
      reject(new Error(`Failed to load ${sources[0]}`));
      return;
    }

    loader(sources[index])
      .then(resolve)
      .catch(() => attempt(index + 1));
  };

  attempt(0);
});

const loadVendorAssets = () => (
  Promise.all(VENDOR_ASSETS.css.map((sources) => loadSequentially(loadStylesheet, sources)))
    .then(() => VENDOR_ASSETS.js.reduce(
      (promise, sources) => promise.then(() => loadSequentially(loadScript, sources)),
      Promise.resolve(),
    ))
);

const toRowArray = (row, columns) => columns.map((column) => (
  Object.prototype.hasOwnProperty.call(row, column.name) ? row[column.name] : null
));

const rowIsEmpty = (row, columns) => columns
  .filter((column) => column.name !== 'id')
  .every((column) => {
    const value = row[column.name];
    return value === undefined || value === null || value === '';
  });

const clientValidate = (rows, columns) => {
  const errors = {};
  rows.forEach((row, index) => {
    const rowNumber = index + 1;
    if (!row.id && rowIsEmpty(row, columns)) {
      return;
    }

    columns.forEach((column) => {
      if (column.readOnly || !column.required) {
        return;
      }
      const value = row[column.name];
      if (value === undefined || value === null || value === '') {
        if (!errors[rowNumber]) {
          errors[rowNumber] = {};
        }
        errors[rowNumber][column.name] = 'Required';
      }
    });
  });
  return errors;
};

const showStatus = (element, message, level) => {
  element.textContent = message;
  element.className = `alert alert-${level}`;
  element.style.display = 'block';
};

const hideStatus = (element) => {
  element.textContent = '';
  element.className = 'alert';
  element.style.display = 'none';
};

const applyRowErrors = (spreadsheetInstance, columns, rowErrors) => {
  const indexByName = new Map(columns.map((column, index) => [column.name, index]));
  Object.entries(rowErrors || {}).forEach(([rowIndex, fields]) => {
    const rowNumber = parseInt(rowIndex, 10) - 1;
    if (Number.isNaN(rowNumber) || rowNumber < 0) {
      return;
    }
    Object.keys(fields || {}).forEach((fieldName) => {
      const columnIndex = indexByName.get(fieldName);
      if (columnIndex === undefined) {
        return;
      }
      const cell = spreadsheetInstance.getCellFromCoords(columnIndex, rowNumber);
      if (cell) {
        cell.classList.add('jss-cell-error');
      }
    });
  });
};

const clearCellErrors = (root) => {
  root.querySelectorAll('.jss-cell-error').forEach((cell) => {
    cell.classList.remove('jss-cell-error');
  });
};

const getCsrfToken = () => {
  const match = document.cookie.match(/csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
};

const computeDeletedIds = (rows, knownIds) => {
  const currentIds = new Set(rows.map((row) => row.id).filter((value) => value));
  const deleted = [];
  knownIds.forEach((id) => {
    if (!currentIds.has(id)) {
      deleted.push(id);
    }
  });
  return deleted;
};

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

const normaliseValue = (value) => {
  if (value === null || value === undefined) {
    return '';
  }
  if (Array.isArray(value)) {
    return value.join(', ');
  }
  return String(value);
};

const autoFitColumns = (instance, columns, rows) => {
  const MIN_WIDTH = 70;
  const MAX_WIDTH = 260;
  const PADDING = 20;
  const CHARACTER_WIDTH = 6.5;

  columns.forEach((column, index) => {
    const headerText = column.title || column.name || '';
    let maxChars = headerText.length;

    rows.forEach((row) => {
      if (!row) {
        return;
      }
      const value = normaliseValue(row[column.name]);
      if (value.length > maxChars) {
        maxChars = value.length;
      }
    });

    const width = clamp((maxChars * CHARACTER_WIDTH) + PADDING, MIN_WIDTH, MAX_WIDTH);
    instance.setWidth(index, width);
  });
};

const initialiseSpreadsheet = (wrapper) => {
  const tableElement = wrapper.querySelector('#spreadsheet-table');
  const statusElement = wrapper.querySelector('#spreadsheet-status');
  const saveButton = wrapper.querySelector('#save-spreadsheet');
  const addRowButton = wrapper.querySelector('#add-row-btn');
  const allowCreate = wrapper.dataset.allowCreate === 'true';

  const columnsNode = document.getElementById('spreadsheet-columns');
  const dataNode = document.getElementById('spreadsheet-data');

  if (!columnsNode || !dataNode) {
    showStatus(statusElement, 'Missing spreadsheet configuration.', 'danger');
    return;
  }

  const columns = JSON.parse(columnsNode.textContent);
  const data = JSON.parse(dataNode.textContent);

  const factory = window.jspreadsheet || window.jSpreadsheet;
  if (typeof factory !== 'function') {
    showStatus(statusElement, 'Spreadsheet library failed to load.', 'danger');
    return;
  }

  const instance = factory(tableElement, {
    data: data.map((row) => toRowArray(row, columns)),
    columns,
    freezeColumns: 0,
    tableOverflow: true,
    tableHeight: '600px',
    defaultColWidth: 120,
    allowInsertColumn: false,
    allowDeleteColumn: false,
    allowRenameColumn: false,
    allowInsertRow: allowCreate,
    allowDeleteRow: true,
    allowManualInsertColumn: false,
    allowManualDeleteColumn: false,
    columnDrag: false,
    rowDrag: false,
    toolbar: false,
    contextMenu(instance, x, y) {
      const items = [];
      if (y !== null && y >= 0) {
        const rowNumber = parseInt(y, 10);
        if (allowCreate) {
          items.push({
            title: 'Insert row above',
            onclick: () => instance.insertRow(1, rowNumber),
          });
          items.push({
            title: 'Insert row below',
            onclick: () => instance.insertRow(1, rowNumber + 1),
          });
        }
        items.push({
          title: 'Delete row',
          onclick: () => instance.deleteRow(rowNumber, 1),
        });
        items.push({ type: 'line' });
      }
      items.push({
        title: 'Copy',
        shortcut: 'Ctrl + C',
        onclick: () => document.execCommand('copy'),
      });
      items.push({
        title: 'Paste',
        shortcut: 'Ctrl + V',
        onclick: () => document.execCommand('paste'),
      });
      return items;
    },
  });

  autoFitColumns(instance, columns, data);

  let knownIds = new Set((data || []).map((row) => row.id).filter((value) => value));

  if (addRowButton) {
    addRowButton.addEventListener('click', () => {
      instance.insertRow(1, instance.getData().length);
    });
  }

  const setLoadingState = (isLoading) => {
    saveButton.disabled = isLoading;
    if (addRowButton) {
      addRowButton.disabled = isLoading;
    }
  };

  saveButton.addEventListener('click', () => {
    clearCellErrors(wrapper);
    hideStatus(statusElement);

    const rows = instance.getJson();
    const clientErrors = clientValidate(rows, columns);
    if (Object.keys(clientErrors).length) {
      applyRowErrors(instance, columns, clientErrors);
      showStatus(statusElement, 'Fill required fields highlighted in red before saving.', 'warning');
      return;
    }

    const payload = {
      rows,
      deleted_ids: computeDeletedIds(rows, knownIds),
    };

    setLoadingState(true);

    fetch(window.location.href, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
        'X-Requested-With': 'XMLHttpRequest',
      },
      body: JSON.stringify(payload),
    })
      .then((response) => response.json().then((payloadData) => ({ ok: response.ok, payloadData })))
      .then(({ ok, payloadData }) => {
        if (!ok) {
          const rowErrors = (payloadData.errors && payloadData.errors.rows) || {};
          const nonField = (payloadData.errors && payloadData.errors.non_field_errors) || [];
          applyRowErrors(instance, columns, rowErrors);
          if (nonField.length) {
            showStatus(statusElement, nonField.join('\n'), 'warning');
          } else {
            showStatus(statusElement, 'Some rows could not be saved. Check highlighted cells.', 'warning');
          }
          return;
        }

        const freshRows = payloadData.rows || [];
        instance.setData(freshRows.map((row) => toRowArray(row, columns)));
        autoFitColumns(instance, columns, freshRows);
        knownIds = new Set(freshRows.map((row) => row.id).filter((value) => value));
        showStatus(statusElement, 'All changes saved.', 'success');
      })
      .catch(() => {
        showStatus(statusElement, 'Could not save changes. Please try again.', 'danger');
      })
      .finally(() => {
        setLoadingState(false);
      });
  });
};

const init = () => {
  const wrapper = document.getElementById('spreadsheet-wrapper');
  if (!wrapper) {
    return;
  }

  loadVendorAssets()
    .then(() => {
      initialiseSpreadsheet(wrapper);
    })
    .catch((error) => {
      const statusElement = wrapper.querySelector('#spreadsheet-status');
      showStatus(statusElement, error.message, 'danger');
    });
};

document.addEventListener('DOMContentLoaded', init);
