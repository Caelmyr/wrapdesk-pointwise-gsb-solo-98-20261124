'use strict';

const byId = id => document.getElementById(id);
const values = ['left', 'right', 'bottom_left', 'bottom_right', 'top', 'bottom',
    'diameter', 'height', 'start', 'end', 'density'];
const view = {
    photo: null, report: null, source: null, flat: null, meta: null, occlusions: [],
    draftPoints: [], tool: '', measureStart: null, line: null, pinpoint: null, barcode: null,
};
let busy = false;

function message(text, error = false) {
    byId('status').textContent = text;
    byId('status').classList.toggle('error', error);
}

function run(action) {
    return async event => {
        if (event?.type === 'submit') event.preventDefault();
        if (busy) return;
        busy = true;
        try {
            await action(event);
        } catch (error) {
            message(error.message, true);
        } finally {
            busy = false;
        }
    };
}

async function api(path, method = 'GET', value) {
    const options = {method};
    if (value instanceof FormData) {
        options.body = value;
    } else if (value !== undefined) {
        options.headers = {'Content-Type': 'application/json'};
        options.body = JSON.stringify(value);
    }
    const response = await fetch('/wrapdesk' + path, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '请求没有完成。');
    return result;
}

function loadImage(url) {
    return new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error('图像未能加载。'));
        image.src = url;
    });
}

function route() {
    return '/photos/' + view.photo.id;
}

function displaySettings() {
    const entered = Object.fromEntries(values.map(name => [name, byId(name).valueAsNumber]));
    return view.report ? view.report.calibration : entered;
}

function revision() {
    return 'revision=' + view.photo.revision;
}

function replaceOptions(select, first, items) {
    select.replaceChildren(new Option(first, ''), ...items.map(entry => new Option(entry.title, entry.id)));
}

async function refreshPhotos() {
    const entries = await api('/photos');
    replaceOptions(byId('photo-list'), '打开原照', entries);
    byId('photo-list').value = view.photo?.id || '';
}

async function refreshReports() {
    const entries = await api(route() + '/reports');
    replaceOptions(byId('report-list'), '当前原照', entries);
    byId('report-list').value = view.report?.id || '';
}

function setDownload(name, path) {
    const link = byId(name);
    link.hidden = !path;
    if (path) link.href = '/wrapdesk' + path;
    else link.removeAttribute('href');
}

function drawSource() {
    const canvas = byId('source');
    const image = view.source;
    if (!image) return;
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const context = canvas.getContext('2d');
    context.drawImage(image, 0, 0);
    const c = displaySettings();
    context.lineWidth = Math.max(1.5, canvas.width / 420);
    if ([c.left, c.right, c.bottom_left, c.bottom_right, c.top, c.bottom].every(Number.isFinite)) {
        context.strokeStyle = '#81efd7';
        trace(context, [[c.left, c.top], [c.right, c.top], [c.bottom_right, c.bottom],
            [c.bottom_left, c.bottom], [c.left, c.top]]);
    }
    for (const [x0, y0, x1, y1] of view.occlusions) {
        context.fillStyle = '#e68a4850';
        context.strokeStyle = '#ffb683';
        context.fillRect(x0, y0, x1 - x0, y1 - y0);
        context.strokeRect(x0, y0, x1 - x0, y1 - y0);
    }
    for (const point of view.draftPoints) dot(context, point, '#f1d786');
    if (view.line) {
        const points = [[view.line.samples[0].source_x, view.line.samples[0].source_y],
            [view.line.samples.at(-1).source_x, view.line.samples.at(-1).source_y]];
        context.strokeStyle = '#edb364';
        trace(context, points);
    }
    if (view.pinpoint) dot(context, [view.pinpoint.source_x, view.pinpoint.source_y], '#ffe084');
}

function trace(context, points) {
    context.beginPath();
    context.moveTo(...points[0]);
    for (const point of points.slice(1)) context.lineTo(...point);
    context.stroke();
}

function dot(context, point, color) {
    const size = Math.max(4, context.canvas.width / 130);
    context.strokeStyle = '#173833';
    context.fillStyle = color;
    context.beginPath();
    context.arc(...point, size, 0, Math.PI * 2);
    context.fill();
    context.stroke();
}

function drawFlat() {
    const canvas = byId('flattened');
    canvas.hidden = view.flat === null;
    if (!view.flat) return;
    canvas.width = view.flat.naturalWidth;
    canvas.height = view.flat.naturalHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(view.flat, 0, 0);
    ctx.lineWidth = Math.max(1, canvas.width / 440);
    if (view.line) {
        ctx.strokeStyle = '#926020';
        const endRow = view.line.end_row ?? view.line.row;
        trace(ctx, [[view.line.start_column, view.line.row], [view.line.end_column, endRow]]);
    }
    if (view.measureStart) dot(ctx, view.measureStart, '#f5cc60');
    if (view.pinpoint) dot(ctx, [view.pinpoint.column, view.pinpoint.row], '#ffe084');
}

function renderLine() {
    const line = view.line;
    byId('line-result').hidden = !line;
    byId('clear-line').disabled = !line || Boolean(view.report);
    if (!line) return;
    const endRow = line.end_row ?? line.row;
    const shape = line.slanted ? `斜线 · 第 ${line.row + 1} 行到第 ${endRow + 1} 行` : `第 ${line.row + 1} 行`;
    byId('line-details').textContent =
        `横向弧长 ${line.span_mm.toFixed(2)} mm · 连续实见合计 ${line.observed_mm.toFixed(2)} mm · ${shape}`;
    const canvas = byId('profile');
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = '#236d60';
    ctx.lineWidth = 2;
    ctx.beginPath();
    let continuing = false;
    line.samples.forEach((sample, index) => {
        if (!sample.rgb) {
            continuing = false;
            return;
        }
        const [r, g, b] = sample.rgb;
        const x = 4 + index * (canvas.width - 8) / Math.max(1, line.samples.length - 1);
        const y = 4 + (1 - (0.213 * r + 0.715 * g + 0.072 * b) / 255) * (canvas.height - 8);
        if (continuing) ctx.lineTo(x, y);
        else ctx.moveTo(x, y);
        continuing = true;
    });
    ctx.stroke();
}

function renderBarcodePanel() {
    const panel = byId('barcode-panel');
    panel.hidden = !view.line || Boolean(view.report);
    byId('barcode-adopt').hidden = true;
    const summary = byId('barcode-summary');
    const result = view.barcode;
    if (!result) {
        summary.textContent = view.line
            ? '填入白边、黑条宽度与反差门槛后搜索。'
            : '';
        return;
    }
    if (!result.recommended) {
        summary.textContent =
            `检查了 ${result.checked_lines} 条直线，没有一条满足条件，保留原测线。`;
        return;
    }
    const pick = result.recommended;
    summary.textContent =
        `候选 ${result.qualified_count} / ${result.checked_lines} 条，推荐第 ${pick.row + 1} 行到第 ${pick.end_row + 1} 行`
        + `（${pick.row === pick.end_row ? '水平' : '略斜'}）：最弱反差 ${pick.weakest_contrast}`
        + `，左端实见白边 ${pick.left_margin_columns} 列、右端 ${pick.right_margin_columns} 列`
        + `，最细黑条 ${pick.min_black_span_columns} 个原片列，共过 ${pick.source_columns} 个原片列。`;
    byId('barcode-adopt').hidden = false;
}

function renderGaps() {
    byId('gap-count').textContent = view.occlusions.length ? `${view.occlusions.length} 个缺证框` : '无缺证框。';
    byId('gap-list').replaceChildren();
    view.occlusions.forEach((_, index) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = `移除 ${index + 1}`;
        button.disabled = Boolean(view.report);
        button.addEventListener('click', () => {
            view.occlusions.splice(index, 1);
            renderGaps();
            drawSource();
            message('已修改缺证框，应用后更新平展稿。');
        });
        byId('gap-list').append(button);
    });
}

async function render() {
    const photo = view.photo;
    const report = view.report;
    const calibration = report ? report.calibration : photo.calibration;
    const reportRoute = report && route() + '/reports/' + report.id;
    for (const name of values) {
        byId(name).value = calibration?.[name] ?? ({diameter: 70, height: 50, start: -70, end: 70, density: 5})[name] ?? '';
        byId(name).disabled = Boolean(report);
    }
    view.occlusions = (report ? report.occlusions : photo.occlusions).map(box => [...box]);
    view.tool = '';
    view.draftPoints = [];
    view.line = report?.transect || null;
    view.barcode = null;
    view.pinpoint = null;
    view.measureStart = null;
    view.meta = null;
    view.flat = null;
    if (calibration) {
        const url = report ? reportRoute + '/flatten.png' : route() + '/flatten.png?' + revision();
        [view.meta, view.flat] = await Promise.all([
            report ? Promise.resolve(report.geometry) : api(route() + '/flatten?' + revision()),
            loadImage('/wrapdesk' + url),
        ]);
        setDownload('flat-download', url + (report ? '?' : '&') + 'download');
        const data = view.meta;
        byId('flat-meta').textContent =
            `${data.width} × ${data.height} px · 弧宽 ${data.arc_mm.toFixed(2)} mm · 实见 ${(100 * data.visible_pixels / data.total_pixels).toFixed(1)}%`;
    } else {
        setDownload('flat-download', null);
        byId('flat-meta').textContent = '应用标定后查看。';
    }
    setDownload('line-download', report?.transect ? reportRoute + '/transect.csv' : null);
    setDownload('source-download', route() + '/source.png?download');
    for (const target of ['mark-body', 'mark-gap']) byId(target).disabled = Boolean(report);
    byId('calibration').querySelector('[type="submit"]').disabled = Boolean(report);
    byId('measure').disabled = Boolean(report) || !calibration;
    byId('save').disabled = Boolean(report) || !calibration;
    byId('report-title').disabled = Boolean(report);
    byId('probe-result').textContent = '在平展稿上点一下可在原照核对。';
    byId('photo-name').textContent = photo.title + (report ? ' · ' + report.title : '');
    byId('source-meta').textContent = `${photo.width} × ${photo.height} px`;
    byId('empty').hidden = true;
    byId('desk').hidden = false;
    renderGaps();
    drawSource();
    drawFlat();
    renderLine();
    renderBarcodePanel();
}

async function openPhoto(id) {
    view.photo = await api('/photos/' + id);
    view.report = null;
    view.source = await loadImage('/wrapdesk' + route() + '/source.png');
    await render();
    await Promise.all([refreshPhotos(), refreshReports()]);
    message('原照已打开。');
}

function pixel(event) {
    const canvas = event.currentTarget;
    const box = canvas.getBoundingClientRect();
    return [
        Math.max(0, Math.min(canvas.width - 1, Math.floor((event.clientX - box.left) * canvas.width / box.width))),
        Math.max(0, Math.min(canvas.height - 1, Math.floor((event.clientY - box.top) * canvas.height / box.height))),
    ];
}

byId('start').addEventListener('input', () => message('应用后更新角度范围。'));
byId('end').addEventListener('input', () => message('应用后更新角度范围。'));
for (const name of values.slice(0, 6)) byId(name).addEventListener('input', drawSource);
byId('demo').addEventListener('click', run(async () => {
    const photo = await api('/demo', 'POST');
    await openPhoto(photo.id);
}));
byId('photo-list').addEventListener('change', run(async event => {
    if (event.target.value) await openPhoto(event.target.value);
}));
byId('upload').addEventListener('change', run(async event => {
    if (!event.target.files.length) return;
    const form = new FormData();
    form.append('image', event.target.files[0]);
    const photo = await api('/photos', 'POST', form);
    event.target.value = '';
    await openPhoto(photo.id);
}));
byId('mark-body').addEventListener('click', () => {
    view.tool = 'body';
    view.draftPoints = [];
    drawSource();
    message('按顺序点上左、上右、下左、下右。');
});
byId('mark-gap').addEventListener('click', () => {
    view.tool = 'gap';
    view.draftPoints = [];
    drawSource();
    message('点出遮挡或反光框的两个对角。');
});
byId('source').addEventListener('click', event => {
    if (busy || !view.tool) return;
    view.draftPoints.push(pixel(event));
    const points = view.draftPoints;
    if (view.tool === 'body' && points.length === 4) {
        const [a, b, c, d] = points;
        Object.entries({left: a[0], right: b[0], bottom_left: c[0], bottom_right: d[0],
            top: Math.round((a[1] + b[1]) / 2), bottom: Math.round((c[1] + d[1]) / 2)})
            .forEach(([name, value]) => { byId(name).value = value; });
        view.tool = '';
        view.draftPoints = [];
        message('四角已录入，确认实物尺寸后应用标定。');
    } else if (view.tool === 'gap' && points.length === 2) {
        const [[a, b], [c, d]] = points;
        if (a === c || b === d) message('缺证框需要有宽度和高度。', true);
        else if (view.occlusions.length >= 12) message('最多同时标记 12 个缺证框。', true);
        else {
            view.occlusions.push([Math.min(a, c), Math.min(b, d), Math.max(a, c), Math.max(b, d)]);
            renderGaps();
            message('缺证框已录入，应用后更新平展稿。');
        }
        view.tool = '';
        view.draftPoints = [];
    }
    drawSource();
});
byId('calibration').addEventListener('submit', run(async () => {
    view.photo = await api(route(), 'PUT',
        {revision: view.photo.revision, calibration: displaySettings(), occlusions: view.occlusions});
    await render();
    message('标定与缺证框已应用。');
}));
byId('measure').addEventListener('click', () => {
    view.tool = 'measure';
    view.measureStart = null;
    view.draftPoints = [];
    view.barcode = null;
    drawSource();
    drawFlat();
    renderBarcodePanel();
    message('在平展稿上点两次，同一行从第一点量到第二点的横向位置。');
});
byId('clear-line').addEventListener('click', () => {
    view.line = null;
    view.measureStart = null;
    view.barcode = null;
    renderLine();
    renderBarcodePanel();
    drawSource();
    drawFlat();
});
byId('barcode-search').addEventListener('click', run(async () => {
    const line = view.line;
    if (!line) {
        message('先用量横向弧长定出条码的起止列。', true);
        return;
    }
    const query = new URLSearchParams({
        revision: String(view.photo.revision),
        start: String(line.start_column), end: String(line.end_column), row: String(line.row),
        margin_left: byId('margin-left').value, margin_right: byId('margin-right').value,
        min_bar_columns: byId('min-bar').value, min_contrast: byId('min-contrast').value,
    });
    view.barcode = await api(route() + '/barcode-lines?' + query.toString());
    renderBarcodePanel();
    message(view.barcode.recommended ? '找到可复扫直线，已给出推荐。' : '没有符合条件的直线，保留原测线。');
}));
byId('barcode-adopt').addEventListener('click', run(async () => {
    const pick = view.barcode?.recommended;
    if (!pick) return;
    const line = view.line;
    const query = new URLSearchParams({
        revision: String(view.photo.revision),
        start: String(line.start_column), end: String(line.end_column),
        row: String(pick.row), end_row: String(pick.end_row),
    });
    view.line = await api(route() + '/transect?' + query.toString());
    view.barcode = null;
    renderLine();
    renderBarcodePanel();
    drawSource();
    drawFlat();
    message('推荐直线已采用，逐点亮度按各点实际经过的原照位置读取。');
}));
byId('flattened').addEventListener('click', run(async event => {
    const [column, row] = pixel(event);
    if (view.tool === 'measure') {
        if (!view.measureStart) {
            view.measureStart = [column, row];
            message('在终点的横向位置点一下。');
        } else {
            const [start, fixedRow] = view.measureStart;
            const query = `${revision()}&start=${start}&end=${column}&row=${fixedRow}`;
            view.line = await api(route() + '/transect?' + query);
            view.tool = '';
            view.measureStart = null;
            view.barcode = null;
            renderLine();
            renderBarcodePanel();
            message('横向测线已更新。');
        }
    } else {
        const version = view.report ? 'report=' + view.report.id : revision();
        const point = await api(route() + `/probe?${version}&column=${column}&row=${row}`);
        view.pinpoint = point;
        const source = `原照 (${point.source_x.toFixed(1)}, ${point.source_y.toFixed(1)}) px`;
        const arc = `${point.angle.toFixed(2)}° · 弧距 ${point.arc_mm.toFixed(2)} mm · 高度 ${point.height_mm.toFixed(2)} mm`;
        byId('probe-result').textContent = point.visible
            ? `${source} · ${arc} · RGB ${point.rgb.join('/')}`
            : `${source} · ${arc} · 缺证，无色值`;
    }
    drawSource();
    drawFlat();
}));
byId('archive').addEventListener('submit', run(async () => {
    const line = view.line;
    let transect = null;
    if (line) {
        transect = [line.start_column, line.end_column, line.row];
        if (line.slanted) transect.push(line.end_row);
    }
    view.report = await api(route() + '/reports', 'POST',
        {revision: view.photo.revision, title: byId('report-title').value, transect});
    byId('report-title').value = '';
    await render();
    await refreshReports();
    message('检查稿已封存。');
}));
byId('report-list').addEventListener('change', run(async event => {
    const id = event.target.value;
    view.report = id ? await api(route() + '/reports/' + id) : null;
    await render();
    message(id ? '已打开封存稿。' : '已返回当前原照。');
}));

run(async () => {
    const from = new URLSearchParams(location.search).get('photo');
    if (from) await openPhoto(from);
    else await refreshPhotos();
})();
