// Pure helpers for the legacy results page: the 12 stroke types and 7 groups from the one stroke
// classifier (outputs/<stem>/strokes.json, schema 2). DOM-free so node can test it.

export const STROKE_LABELS_12 = {
  short_serve: ['发短球', 'Short serve'], long_serve: ['发长球', 'Long serve'],
  clear: ['高远球', 'Clear'], lift: ['挑球', 'Lift'], smash: ['杀球', 'Smash'], drop: ['吊球', 'Drop'],
  net_shot: ['放网', 'Net shot'], block: ['挡网', 'Block'], cross_net: ['勾对角', 'Cross-court net'],
  net_rush: ['扑球', 'Net rush'], drive: ['平抽球', 'Drive'], push: ['推球', 'Push']
};

export const GROUP_LABELS = {
  serve: ['发球', 'Serve'], clear: ['高远球', 'Clear'], smash: ['杀球', 'Smash'], drop: ['吊球', 'Drop'],
  drive: ['平抽球', 'Drive'], net: ['网前球', 'Net'], lift: ['挑球', 'Lift']
};

export const GROUP_ORDER = ['serve', 'clear', 'lift', 'smash', 'drop', 'drive', 'net'];

const NOTICES = {
  unverified: ['击球类型识别尚未在你的视频上验证准确率。', 'Stroke accuracy not yet verified on your footage.'],
  stale: ['击球标签来自旧版识别器，请重新标注。', 'Stroke labels out of date — re-label this result.'],
  no_model: ['未安装击球识别模型。', 'Stroke model not installed.']
};
const NO_REFERENCE = ['暂无参考范围，未评分。', 'No reference ranges yet — not scored.'];

function pick(pair, lang) { return lang === 'zh' ? pair[0] : pair[1]; }
function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }

export function isStrokeType(id) { return has(STROKE_LABELS_12, id); }

export function strokeLabel(id, lang) {
  if (id === 'unknown') return lang === 'zh' ? '不确定' : 'Uncertain';
  if (isStrokeType(id)) return pick(STROKE_LABELS_12[id], lang);
  return String(id == null ? '' : id).replace(/_/g, ' ');
}

function isStale(d) { return !d || d.schema !== 2; }

export function strokeNotice(d) {
  if (isStale(d)) return 'stale';
  if (d.classifier === null) return 'no_model';
  if (!d.classifier || d.classifier.validated_on_owner_footage !== true) return 'unverified';
  return null;
}

export function timelineItems(d, lang) {
  if (isStale(d) || !Array.isArray(d.strokes)) return [];
  return d.strokes.map(function (s) {
    var id = s && s.stroke;
    return { text: strokeLabel(id, lang), muted: id === 'unknown' };
  });
}

export function distributionText(d, lang) {
  if (isStale(d)) return '';
  var groups = d.groups && typeof d.groups === 'object' ? d.groups : {};
  return GROUP_ORDER.filter(function (g) { return groups[g] > 0; })
    .map(function (g) { return pick(GROUP_LABELS[g], lang) + ' ' + groups[g]; }).join(' · ');
}

// Technique reports (strokes.jsonl, from /api/technique) written before the one stroke classifier
// carry no `scored` key: their labels came from the old rule classifier, so none is shown.
export function techniqueStale(d) {
  var reports = d && Array.isArray(d.strokes) ? d.strokes : [];
  return reports.some(function (r) { return !r || typeof r !== 'object' || !has(r, 'scored'); });
}

export function noticeText(kind, lang) { return has(NOTICES, kind) ? pick(NOTICES[kind], lang) : ''; }

export function noReferenceText(lang) { return pick(NO_REFERENCE, lang); }
