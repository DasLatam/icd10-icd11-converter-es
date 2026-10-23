<?php
/*
 * API REST /api/v1 del conversor CIE-10 ↔ CIE-11, versión PHP para hosting
 * compartido (Apache + PHP ≥ 7.4 con pdo_sqlite e intl; SQLite ≥ 3.9 con FTS5).
 *
 * Es un port de app/api.py + app/datos.py: mismas rutas, mismos campos, mismos
 * errores. tests/paridad_php.py compara las dos respuesta por respuesta.
 * Diferencias a propósito: no hay límite de pedidos propio ni claves de API
 * (en un hosting compartido no hay memoria entre pedidos; lo controla el
 * hosting) y no se cuentan pedidos. No se guarda nada de lo consultado.
 *
 * Apache rechaza con 404 una barra codificada (%2F) en la ruta si el servidor no
 * tiene AllowEncodedSlashes: los id de la CIE-11 con barra (761947693/unspecified)
 * van con la barra tal cual, /api/v1/cie11/761947693/unspecified.
 *
 * Archivos que arma scripts/construir_estatico.py al lado de este:
 *   cie.db        la base (sólo lectura)
 *   textos.json   rótulos y avisos, exportados de app/datos.py
 *   meta.json     respuesta de /meta, generada con la app Python
 *   openapi.json  la especificación, con la URL pública ya puesta
 */
declare(strict_types=1);
ini_set('display_errors', '0');   // un aviso de PHP en el medio rompe el JSON

const LOTE_MAXIMO = 100;
const CUERPO_MAXIMO = 65536;

$DIR = __DIR__;
$T = json_decode((string)file_get_contents("$DIR/textos.json"), true);

// ------------------------------------------------------------------ salida

function cabeceras_comunes(): void {
    header('X-Content-Type-Options: nosniff');
    header('Referrer-Policy: no-referrer');
    header('X-Frame-Options: DENY');
    header('Cross-Origin-Resource-Policy: cross-origin');
    header('Access-Control-Allow-Origin: *');
    header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
    header('Access-Control-Allow-Headers: Content-Type, X-API-Key, If-None-Match');
    header('Access-Control-Expose-Headers: ETag, Retry-After');
    header('Access-Control-Max-Age: 86400');
    global $T;
    header('X-Datos-Version: ' . $T['version_datos']);
}

function codificar($datos): string {
    return json_encode($datos, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRESERVE_ZERO_FRACTION);
}

function responder($datos, int $estado = 200, bool $sin_cache = false, ?string $crudo = null): void {
    global $T;
    $cuerpo = $crudo ?? codificar($datos);
    http_response_code($estado);
    header('Content-Type: application/json');
    $metodo = $_SERVER['REQUEST_METHOD'] ?? 'GET';
    if ($estado === 200 && ($metodo === 'GET' || $metodo === 'HEAD')) {
        $etag = '"' . substr(hash('sha256', $T['version_datos'] . $cuerpo), 0, 32) . '"';
        header("ETag: $etag");
        header('Cache-Control: ' . ($sin_cache ? 'no-store' : 'public, max-age=86400'));
        $pedidos = array_map('trim', explode(',', $_SERVER['HTTP_IF_NONE_MATCH'] ?? ''));
        if (in_array($etag, $pedidos, true) || in_array("W/$etag", $pedidos, true)) {
            http_response_code(304);
            header_remove('Content-Type');
            exit;
        }
    } else {
        header('Cache-Control: no-store');
    }
    echo $cuerpo;
    exit;
}

class ErrorApi extends Exception {
    public int $estado; public string $codigo; public $detalle;
    function __construct(int $estado, string $codigo, string $mensaje, $detalle = null) {
        parent::__construct($mensaje);
        $this->estado = $estado; $this->codigo = $codigo; $this->detalle = $detalle;
    }
}

function error_json(int $estado, string $codigo, string $mensaje, $detalle = null): void {
    $e = ['estado' => $estado, 'codigo' => $codigo, 'mensaje' => $mensaje];
    if ($detalle !== null) $e['detalle'] = $detalle;
    responder(['error' => $e], $estado);
}

// ------------------------------------------------------------------ base

function db(): PDO {
    static $c = null;
    if ($c === null) {
        $opciones = [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC];
        if (class_exists('Pdo\\Sqlite')) $opciones[constant('Pdo\\Sqlite::ATTR_OPEN_FLAGS')] = constant('Pdo\\Sqlite::OPEN_READONLY');
        elseif (defined('PDO::SQLITE_ATTR_OPEN_FLAGS')) $opciones[constant('PDO::SQLITE_ATTR_OPEN_FLAGS')] = constant('PDO::SQLITE_OPEN_READONLY');
        $c = new PDO('sqlite:' . __DIR__ . '/cie.db', null, null, $opciones);
        $c->setAttribute(PDO::ATTR_STRINGIFY_FETCHES, false);
    }
    return $c;
}

function uno(string $sql, array $p = []): ?array {
    $s = db()->prepare($sql); $s->execute($p);
    $r = $s->fetch();
    return $r === false ? null : $r;
}

function todos(string $sql, array $p = []): array {
    $s = db()->prepare($sql); $s->execute($p);
    return $s->fetchAll();
}

function entero($v): ?int { return $v === null ? null : (int)$v; }
function booleano_q(string $nombre): bool {
    return in_array(strtolower($_GET[$nombre] ?? ''), ['1', 'true', 'si', 'sí', 'yes'], true);
}

function normalizar(string $texto): string {
    $t = class_exists('Normalizer') ? Normalizer::normalize($texto, Normalizer::FORM_KD) : $texto;
    $t = preg_replace('/\p{Mn}+/u', '', (string)$t);
    $t = mb_strtolower((string)$t, 'UTF-8');
    return trim((string)preg_replace('/\s+/u', ' ', $t));
}

// ------------------------------------------------------------------ códigos

function codigo10(string $texto): string {
    $t = strtoupper((string)preg_replace('/\s/u', '', $texto));
    $t = str_replace('–', '-', $t);
    if (preg_match('/^[A-Z]\d\d[0-9X]$/', $t)) $t = $t[3] === 'X' ? substr($t, 0, 3) : substr($t, 0, 3) . '.' . $t[3];
    if (preg_match('/^[A-Z]\d\d\.X$/', $t)) $t = substr($t, 0, 3);
    return $t;
}

function codigo11(string $texto): string {
    return strtoupper((string)preg_replace('/\s/u', '', $texto));
}

function cap10(?string $cod): ?array {
    if (!$cod) return null;
    $r = uno('SELECT codigo, titulo FROM cie10 WHERE codigo=?', [$cod]);
    return $r ? ['codigo' => $r['codigo'], 'titulo' => $r['titulo']] : null;
}

function cap11(?string $num): ?array {
    $r = uno("SELECT id, titulo FROM cie11 WHERE tipo='capitulo' AND capitulo=?", [$num]);
    return $r ? ['codigo' => $num, 'titulo' => $r['titulo'], 'id' => $r['id']] : null;
}

function ficha10(array $f, bool $completa = true): array {
    $d = [
        'sistema' => 'CIE-10', 'codigo' => $f['codigo'], 'titulo' => $f['titulo'], 'tipo' => $f['tipo'],
        'nivel' => entero($f['nivel']), 'valido_para_codificar' => (bool)$f['valido'],
        'capitulo' => $f['capitulo'] ? cap10($f['capitulo']) : null,
        'grupo' => $f['grupo'] ? cap10($f['grupo']) : null,
        'padre' => $f['padre'],
    ];
    if ($completa) {
        $d['ruta'] = ancestros10($f['codigo']);
        $d['en_tablas_oms'] = (bool)$f['en_tabla_oms'];
        $d['daga'] = (bool)$f['daga'];
        $d['asterisco'] = (bool)$f['asterisco'];
        if ($f['modificacion'] && $f['modificacion'] !== 'NO') $d['historial_fuente'] = $f['modificacion'];
        if (strpos((string)$f['modificacion'], 'CEMECE') !== false)
            $d['uso_nacional'] = 'Código agregado por acuerdo del CEMECE (México); no es de la OMS.';
    }
    return $d;
}

function ficha11(array $f, bool $completa = true): array {
    global $T;
    $d = [
        'sistema' => 'CIE-11', 'codigo' => $f['codigo'], 'id' => $f['id'], 'titulo' => $f['titulo'],
        'tipo' => $f['tipo'], 'nivel' => entero($f['nivel']), 'capitulo' => cap11($f['capitulo']),
        'uri' => $f['uri_linearizacion'], 'uri_fundacion' => $f['uri_fundacion'],
        'residual' => (bool)$f['residual'], 'hoja' => (bool)$f['hoja'],
        'navegador_oms' => $T['navegador_oms'] . $f['id'],
    ];
    if ($completa) {
        $d['ruta'] = ancestros11($f['id']);
        $d['nota_codificacion'] = $f['nota'];
        if ($f['capitulo'] === 'X') $d['extension'] = 'Código de extensión: sólo se usa combinado con un código base.';
    }
    return $d;
}

function buscar_codigo10(string $cod): ?array {
    return uno('SELECT * FROM cie10 WHERE codigo=?', [codigo10($cod)]);
}

function buscar_codigo11(string $cod): ?array {
    $r = uno('SELECT * FROM cie11 WHERE codigo=?', [codigo11($cod)]);
    if ($r === null) $r = uno('SELECT * FROM cie11 WHERE id=?', [trim($cod)]);
    if ($r === null) $r = uno('SELECT * FROM cie11 WHERE bloque_id=?', [trim($cod)]);
    return $r;
}

function ancestros10(string $cod): array {
    $ruta = []; $vistos = [];
    $r = uno('SELECT padre FROM cie10 WHERE codigo=?', [$cod]);
    $p = $r ? $r['padre'] : null;
    while ($p && !isset($vistos[$p])) {
        $vistos[$p] = true;
        $f = uno('SELECT codigo, titulo, tipo, padre FROM cie10 WHERE codigo=?', [$p]);
        if (!$f) break;
        array_unshift($ruta, ['codigo' => $f['codigo'], 'titulo' => $f['titulo'], 'tipo' => $f['tipo']]);
        $p = $f['padre'];
    }
    return $ruta;
}

function ancestros11(string $id): array {
    $ruta = []; $vistos = [];
    $r = uno('SELECT padre FROM cie11 WHERE id=?', [$id]);
    $p = $r ? $r['padre'] : null;
    while ($p && !isset($vistos[$p])) {
        $vistos[$p] = true;
        $f = uno('SELECT id, codigo, bloque_id, titulo, tipo, padre, capitulo FROM cie11 WHERE id=?', [$p]);
        if (!$f) break;
        array_unshift($ruta, ['codigo' => $f['codigo'] ?: ($f['tipo'] === 'capitulo' ? $f['capitulo'] : null),
                              'id' => $f['id'], 'titulo' => $f['titulo'], 'tipo' => $f['tipo']]);
        $p = $f['padre'];
    }
    return $ruta;
}

function hijos10(string $cod): array {
    return array_map(fn($r) => ficha10($r, false), todos('SELECT * FROM cie10 WHERE padre=? ORDER BY orden', [$cod]));
}
function hijos11(string $id): array {
    return array_map(fn($r) => ficha11($r, false), todos('SELECT * FROM cie11 WHERE padre=? ORDER BY orden', [$id]));
}

// ------------------------------------------------------------------ búsqueda

/** difflib.get_close_matches de Python: ratio = 2·M/T con el mismo algoritmo de bloques. */
function bloques_iguales(string $a, string $b): int {
    $la = strlen($a); $lb = strlen($b);
    if ($la === 0 || $lb === 0) return 0;
    // la coincidencia más larga; ante empate, la primera en $a y después en $b (como SequenceMatcher)
    $mejor = 0; $ia = 0; $ib = 0;
    $prev = array_fill(0, $lb + 1, 0);
    for ($i = 1; $i <= $la; $i++) {
        $cur = array_fill(0, $lb + 1, 0);
        for ($j = 1; $j <= $lb; $j++) {
            if ($a[$i - 1] === $b[$j - 1]) {
                $cur[$j] = $prev[$j - 1] + 1;
                if ($cur[$j] > $mejor) { $mejor = $cur[$j]; $ia = $i - $mejor; $ib = $j - $mejor; }
            }
        }
        $prev = $cur;
    }
    if ($mejor === 0) return 0;
    return $mejor + bloques_iguales(substr($a, 0, $ia), substr($b, 0, $ib))
                  + bloques_iguales(substr($a, $ia + $mejor), substr($b, $ib + $mejor));
}

function parecidas(string $palabra, array $candidatos, float $corte): ?string {
    $mejor = null; $puntaje = -1.0; $lp = strlen($palabra);
    foreach ($candidatos as $c) {
        $c = (string)$c;
        $total = $lp + strlen($c);
        if ($total === 0) continue;
        if (2.0 * min($lp, strlen($c)) / $total < $corte) continue;      // real_quick_ratio
        $r = 2.0 * bloques_iguales($c, $palabra) / $total;
        if ($r >= $corte && ($r > $puntaje || ($r === $puntaje && strcmp($c, (string)$mejor) > 0))) {
            $mejor = $c; $puntaje = $r;
        }
    }
    return $mejor;
}

function vocabulario(string $sistema): array {
    static $cache = [];
    if (!isset($cache[$sistema])) {
        $por_letra = [];
        foreach (todos('SELECT palabra FROM vocabulario WHERE sistema=? ORDER BY veces DESC', [$sistema]) as $r) {
            $por_letra[substr($r['palabra'], 0, 1)][] = $r['palabra'];
        }
        $cache[$sistema] = $por_letra;
    }
    return $cache[$sistema];
}

function corregir(string $sistema, array $palabras): ?array {
    $vocab = vocabulario($sistema);
    $salida = []; $cambio = false;
    foreach ($palabras as $p) {
        if (strlen($p) < 4) { $salida[] = $p; continue; }
        $cand = $vocab[substr($p, 0, 1)] ?? [];
        if (in_array($p, $cand, true)) { $salida[] = $p; continue; }
        $cerca = parecidas($p, $cand, 0.75);
        if ($cerca === null && strlen($p) > 4) {
            $todas = [];
            foreach ($vocab as $lista) foreach ($lista as $w) if (abs(strlen($w) - strlen($p)) <= 2) $todas[] = $w;
            $cerca = parecidas($p, $todas, 0.8);
        }
        if ($cerca !== null) { $salida[] = $cerca; $cambio = $cambio || $cerca !== $p; }
        else $salida[] = $p;
    }
    return $cambio ? $salida : null;
}

function buscar(string $sistema, string $q, ?string $capitulo, ?string $tipo, bool $solo_validos,
                int $pagina, int $por_pagina): array {
    $tabla = $sistema === 'cie10' ? 'cie10' : 'cie11';
    $clave = $sistema === 'cie10' ? 'codigo' : 'id';
    $texto = trim($q);
    $filtros = []; $params = [];
    if ($capitulo) {
        $filtros[] = 't.capitulo = ?';
        $params[] = $sistema === 'cie10' ? strtoupper($capitulo)
                  : (ctype_digit($capitulo) ? str_pad($capitulo, 2, '0', STR_PAD_LEFT) : strtoupper($capitulo));
    }
    if ($tipo) { $filtros[] = 't.tipo = ?'; $params[] = $tipo; }
    if ($solo_validos) $filtros[] = $sistema === 'cie10' ? 't.valido = 1' : 't.hoja = 1';
    $extra = $filtros ? ' AND ' . implode(' AND ', $filtros) : '';
    $desde = ($pagina - 1) * $por_pagina;
    $correccion = null; $modo = 'texto';

    if (preg_match('/^[A-Za-z0-9]{1,4}(\.[A-Za-z0-9]{0,4})?$/', $texto) && preg_match('/\d/', $texto)) {
        $modo = 'codigo';
        if ($sistema === 'cie10') { $pref = str_replace('.', '', codigo10($texto)); $cond = "t.codigo_compacto LIKE ? || '%'"; }
        else { $pref = codigo11($texto); $cond = "t.codigo LIKE ? || '%'"; }
        $base = "FROM $tabla t WHERE $cond$extra";
        $total = (int)uno("SELECT count(*) AS n $base", array_merge([$pref], $params))['n'];
        $filas = todos("SELECT t.* $base ORDER BY length(t.codigo), t.orden LIMIT ? OFFSET ?",
                       array_merge([$pref], $params, [$por_pagina, $desde]));
    } else {
        preg_match_all('/[a-z0-9]+/', normalizar($texto), $m);
        $palabras = array_slice($m[0], 0, 8);
        if (!$palabras) return ['total' => 0, 'pagina' => $pagina, 'por_pagina' => $por_pagina, 'resultados' => [], 'modo' => $modo];
        $consultar = function (array $pal) use ($tabla, $clave, $extra, $params, $por_pagina, $desde) {
            $expr = implode(' AND ', array_map(fn($p) => "\"$p\"*", $pal));
            $base = "FROM {$tabla}_fts f JOIN $tabla t ON t.$clave = f.$clave WHERE {$tabla}_fts MATCH ?$extra";
            $tot = (int)uno("SELECT count(*) AS n $base", array_merge([$expr], $params))['n'];
            $orden = "bm25({$tabla}_fts) + (CASE WHEN f.texto LIKE ? THEN -5 ELSE 0 END) "
                   . "+ (CASE t.tipo WHEN 'categoria' THEN -1 WHEN 'subcategoria' THEN 0 ELSE 1 END)";
            $fil = todos("SELECT t.* $base ORDER BY $orden, t.orden LIMIT ? OFFSET ?",
                         array_merge([$expr], $params, ['%' . $pal[0] . '%', $por_pagina, $desde]));
            return [$tot, $fil];
        };
        [$total, $filas] = $consultar($palabras);
        if ($total === 0) {
            $corregidas = corregir($sistema, $palabras);
            if ($corregidas) {
                [$total, $filas] = $consultar($corregidas);
                if ($total) { $correccion = implode(' ', $corregidas); $modo = 'texto_corregido'; }
            }
        }
    }
    $fichar = $sistema === 'cie10' ? 'ficha10' : 'ficha11';
    return ['total' => $total, 'pagina' => $pagina, 'por_pagina' => $por_pagina, 'modo' => $modo,
            'correccion' => $correccion, 'resultados' => array_map(fn($r) => $fichar($r, false), $filas)];
}

// ------------------------------------------------------------------ conversión

function destino11(array $e): array {
    $d = ['codigo' => $e['destino'], 'id' => $e['destino_id'], 'titulo' => null, 'capitulo' => null, 'ruta' => []];
    if ($e['destino_id']) {
        $f = uno('SELECT * FROM cie11 WHERE id=?', [$e['destino_id']]);
        if ($f) $d = ficha11($f);
    }
    if ($e['postcoordinacion']) {
        $partes = [];
        foreach (preg_split('/([&\/])/', $e['postcoordinacion'], -1, PREG_SPLIT_DELIM_CAPTURE) as $cod) {
            if ($cod === '&' || $cod === '/' || $cod === '') continue;
            $f = uno('SELECT codigo, titulo, capitulo FROM cie11 WHERE codigo=?', [$cod]);
            $partes[] = ['codigo' => $cod, 'titulo' => $f ? $f['titulo'] : null, 'extension' => (bool)($f && $f['capitulo'] === 'X')];
        }
        $d['postcoordinacion'] = ['cluster' => $e['postcoordinacion'], 'partes' => $partes];
    }
    return $d;
}

function destino10(array $e): array {
    $f = uno('SELECT * FROM cie10 WHERE codigo=?', [$e['destino']]);
    if ($f) return ficha10($f);
    return ['sistema' => 'CIE-10', 'codigo' => $e['destino'], 'titulo' => null,
            'nota' => 'Código de la tabla de la OMS que no figura en la CIE-10 en español de la fuente.'];
}

function equivalencia(array $e, array $destino): array {
    global $T;
    return $destino + ['correspondencia' => [
        'tipo' => $e['tipo'], 'rotulo' => $T['rotulos'][$e['tipo']], 'explicacion' => $T['explicacion'][$e['tipo']],
        'principal' => (bool)$e['principal'], 'ida_y_vuelta' => (bool)$e['ida_y_vuelta'],
        'prioridad' => entero($e['prioridad']), 'puntaje' => entero($e['puntaje']), 'tabla_oms' => $e['tabla'],
        'observaciones' => $e['observaciones'],
    ]];
}

function sugerencias(string $tabla, string $titulo, int $n = 5): array {
    preg_match_all('/[a-z0-9]+/', normalizar($titulo), $m);
    $palabras = array_slice(array_values(array_filter($m[0], fn($p) => strlen($p) > 3)), 0, 6);
    if (!$palabras) return [];
    $clave = $tabla === 'cie10' ? 'codigo' : 'id';
    $expr = implode(' OR ', array_map(fn($p) => "\"$p\"", $palabras));
    $filas = todos("SELECT t.* FROM {$tabla}_fts f JOIN $tabla t ON t.$clave=f.$clave WHERE {$tabla}_fts MATCH ? "
                 . "AND t.tipo IN ('categoria','subcategoria') ORDER BY bm25({$tabla}_fts) LIMIT ?", [$expr, $n]);
    $fichar = $tabla === 'cie10' ? 'ficha10' : 'ficha11';
    return array_map(fn($f) => $fichar($f, false) + ['sugerencia_automatica' =>
        'Coincidencia de texto calculada por este servicio. NO es una equivalencia oficial de la OMS.'], $filas);
}

function convertir(string $sentido, string $codigo, bool $con_sugerencias = true): ?array {
    global $T;
    $heredada = null;
    if ($sentido === 'cie10-a-cie11') {
        $f = buscar_codigo10($codigo);
        $cod = $f ? $f['codigo'] : codigo10($codigo);
        $filas = todos("SELECT * FROM equivalencias WHERE sentido='10a11' AND origen=? ORDER BY prioridad", [$cod]);
        if ($f === null && !$filas) return null;
        $origen = $f ? ficha10($f) : ['sistema' => 'CIE-10', 'codigo' => $cod, 'titulo' => null,
                                      'nota' => 'Código de la tabla de la OMS sin título en español en la fuente.'];
        if (!$filas && strpos($cod, '.') !== false) {
            $madre = substr($cod, 0, 3);
            $filas = todos("SELECT * FROM equivalencias WHERE sentido='10a11' AND origen=? ORDER BY prioridad", [$madre]);
            if ($filas) $heredada = ['codigo' => $madre, 'titulo' => (cap10($madre) ?? [])['titulo'] ?? null,
                'motivo' => 'La tabla oficial no trae esta subcategoría; se muestra la correspondencia de su categoría de tres caracteres.'];
        }
        $eqs = [];
        foreach ($filas as $e) if ($e['tipo'] !== 'sin_equivalente') $eqs[] = equivalencia($e, destino11($e));
        $sistema_dest = 'cie11';
    } else {
        $f = buscar_codigo11($codigo);
        if ($f === null) return null;
        $origen = ficha11($f);
        $filas = todos("SELECT * FROM equivalencias WHERE sentido='11a10' AND origen=? ORDER BY prioridad", [$f['id']]);
        if (!$filas && $f['padre']) {
            $p = uno('SELECT * FROM cie11 WHERE id=?', [$f['padre']]);
            $filas = todos("SELECT * FROM equivalencias WHERE sentido='11a10' AND origen=? ORDER BY prioridad", [$f['padre']]);
            if ($filas && $p) $heredada = ['codigo' => $p['codigo'], 'id' => $p['id'], 'titulo' => $p['titulo'],
                'motivo' => 'La tabla oficial no trae esta entidad; se muestra la de su entidad madre.'];
        }
        $eqs = [];
        foreach ($filas as $e) if ($e['tipo'] !== 'sin_equivalente') $eqs[] = equivalencia($e, destino10($e));
        $sistema_dest = 'cie10';
    }
    usort($eqs, fn($a, $b) => [-$a['correspondencia']['puntaje'], $a['correspondencia']['prioridad']]
                          <=> [-$b['correspondencia']['puntaje'], $b['correspondencia']['prioridad']]);
    $sin = !$eqs;
    $r = ['sentido' => $sentido, 'origen' => $origen, 'equivalencias' => $eqs, 'total' => count($eqs),
          'sin_equivalente' => $sin, 'heredada_de' => $heredada, 'aviso' => $T['aviso']];
    if ($sin) {
        $nota = null;
        foreach ($filas as $e) if ($e['tipo'] === 'sin_equivalente') { $nota = $e['observaciones']; break; }
        $r['sin_equivalente_motivo'] = $nota ?: 'Las tablas oficiales de la OMS no traen correspondencia.';
        if ($con_sugerencias && !empty($origen['titulo'])) {
            $r['sugerencias_automaticas'] = [
                'aviso' => 'SUGERENCIA AUTOMÁTICA NO OFICIAL: coincidencia de texto entre títulos, sin validar. No usar para codificar sin revisión.',
                'resultados' => sugerencias($sistema_dest, $origen['titulo']),
            ];
        }
    }
    return $r;
}

// ------------------------------------------------------------------ parámetros

function param_entero(string $nombre, int $defecto, int $min, int $max): int {
    $v = $_GET[$nombre] ?? null;
    if ($v === null || $v === '') return $defecto;
    if (!preg_match('/^\s*[+-]?\d+\s*$/', (string)$v))
        throw new ErrorApi(400, 'parametro_invalido', "«{$nombre}» tiene que ser un número entero.");
    $n = (int)$v;
    if ($n < $min || $n > $max)
        throw new ErrorApi(400, 'parametro_invalido', "«{$nombre}» tiene que estar entre $min y $max.");
    return $n;
}

function validar_codigo(string $c, string $sistema): string {
    $c = trim($c);
    if ($c === '' || mb_strlen($c) > 40 || !preg_match('/^[A-Za-z0-9.\-\/&–]+$/u', $c))
        throw new ErrorApi(400, 'codigo_invalido', '«' . mb_substr($c, 0, 40) . "» no tiene forma de código $sistema.");
    return $c;
}

function params_busqueda(): array {
    $q = trim((string)($_GET['q'] ?? ''));
    if ($q === '') throw new ErrorApi(400, 'falta_q', 'Falta el parámetro «q» (código o texto a buscar).');
    if (mb_strlen($q) > 100) throw new ErrorApi(400, 'q_demasiado_largo', '«q» admite hasta 100 caracteres.');
    return ['q' => $q, 'capitulo' => ($_GET['capitulo'] ?? '') ?: null, 'tipo' => ($_GET['tipo'] ?? '') ?: null,
            'solo_validos' => booleano_q('solo_validos'),
            'pagina' => param_entero('pagina', 1, 1, 500), 'por_pagina' => param_entero('por_pagina', 20, 1, 50)];
}

function fila10(string $codigo): array {
    $f = buscar_codigo10(validar_codigo($codigo, 'CIE-10'));
    if ($f === null) throw new ErrorApi(404, 'codigo_inexistente', "El código CIE-10 «{$codigo}» no existe en la fuente.");
    return $f;
}

function fila11(string $codigo): array {
    global $T;
    $f = buscar_codigo11(validar_codigo($codigo, 'CIE-11'));
    if ($f === null) throw new ErrorApi(404, 'codigo_inexistente', "El código CIE-11 «{$codigo}» no existe en la versión {$T['version_cie11']}.");
    return $f;
}

// ------------------------------------------------------------------ rutas

function ruta_pedida(): string {
    $uri = (string)parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH);
    $i = strpos($uri, '/api/v1/');
    $resto = $i === false ? '' : substr($uri, $i + strlen('/api/v1/'));
    return rawurldecode($resto);
}

function atender(): void {
    global $T, $DIR;
    $metodo = $_SERVER['REQUEST_METHOD'] ?? 'GET';
    $r = ruta_pedida();
    if ($metodo === 'OPTIONS') { http_response_code(204); exit; }
    $get = $metodo === 'GET' || $metodo === 'HEAD';
    $SENTIDOS = ['cie10-a-cie11' => 'CIE-10', 'cie11-a-cie10' => 'CIE-11'];

    if ($r === 'convertir/lote') {
        // Igual que Flask: GET a esta ruta no coincide con ninguna regla → 404.
        if ($metodo !== 'POST') throw new ErrorApi(404, 'ruta_inexistente', 'No existe esa ruta de la API. Ver /api/v1/openapi.json.');
        lote($SENTIDOS);
    }
    $existe = preg_match('#^(salud|meta|openapi\.json|cie1[01]/(buscar|capitulos)|cie10/[^/]+(/hijos|/ancestros)?|cie11/.+|convertir/[^/]+/.+)$#', $r);
    if (!$existe) throw new ErrorApi(404, 'ruta_inexistente', 'No existe esa ruta de la API. Ver /api/v1/openapi.json.');
    if (!$get) throw new ErrorApi(405, 'metodo_no_permitido', "Método $metodo no permitido en esta ruta.");

    if ($r === 'salud') {
        try {
            $n = (int)uno('SELECT count(*) AS n FROM cie10')['n'];
            $m = (int)uno('SELECT count(*) AS n FROM cie11')['n'];
        } catch (Throwable $e) {
            throw new ErrorApi(503, 'base_no_disponible', 'La base de datos no responde.');
        }
        responder(['estado' => 'ok', 'version_datos' => $T['version_datos'], 'cie10' => $n, 'cie11' => $m], 200, true);
    }
    if ($r === 'meta') responder(null, 200, false, (string)file_get_contents("$DIR/meta.json"));
    if ($r === 'openapi.json') responder(null, 200, false, (string)file_get_contents("$DIR/openapi.json"));

    if ($r === 'cie10/buscar' || $r === 'cie11/buscar') {
        $p = params_busqueda();
        $sistema = substr($r, 0, 5);
        $tipos = $sistema === 'cie10' ? ['capitulo', 'grupo', 'categoria', 'subcategoria'] : ['capitulo', 'bloque', 'categoria'];
        if ($p['tipo'] && !in_array($p['tipo'], $tipos, true))
            throw new ErrorApi(400, 'parametro_invalido', $sistema === 'cie10'
                ? '«tipo» admite capitulo, grupo, categoria o subcategoria.' : '«tipo» admite capitulo, bloque o categoria.');
        responder(['sistema' => $sistema === 'cie10' ? 'CIE-10' : 'CIE-11', 'q' => $p['q']]
                  + buscar($sistema, $p['q'], $p['capitulo'], $p['tipo'], $p['solo_validos'], $p['pagina'], $p['por_pagina']));
    }
    if ($r === 'cie10/capitulos')
        responder(['sistema' => 'CIE-10', 'capitulos' => array_map(fn($x) => ficha10($x, false),
                   todos("SELECT * FROM cie10 WHERE tipo='capitulo' ORDER BY orden"))]);
    if ($r === 'cie11/capitulos')
        responder(['sistema' => 'CIE-11', 'capitulos' => array_map(fn($x) => ficha11($x, false),
                   todos("SELECT * FROM cie11 WHERE tipo='capitulo' ORDER BY orden"))]);

    if (preg_match('#^cie10/([^/]+)/(hijos|ancestros)$#', $r, $m)) {
        $f = fila10($m[1]);
        responder($m[2] === 'hijos' ? ['codigo' => $f['codigo'], 'hijos' => hijos10($f['codigo'])]
                                    : ['codigo' => $f['codigo'], 'ancestros' => ancestros10($f['codigo'])]);
    }
    if (preg_match('#^cie10/([^/]+)$#', $r, $m)) {
        $f = fila10($m[1]);
        $d = ficha10($f);
        $d['hijos'] = hijos10($f['codigo']);
        $d['equivalencias'] = "/api/v1/convertir/cie10-a-cie11/{$f['codigo']}";
        responder($d);
    }
    if (preg_match('#^cie11/(.+)/(hijos|ancestros)$#', $r, $m)) {
        $f = fila11($m[1]);
        responder($m[2] === 'hijos' ? ['codigo' => $f['codigo'], 'id' => $f['id'], 'hijos' => hijos11($f['id'])]
                                    : ['codigo' => $f['codigo'], 'id' => $f['id'], 'ancestros' => ancestros11($f['id'])]);
    }
    if (preg_match('#^cie11/(.+)$#', $r, $m)) {
        $f = fila11($m[1]);
        $d = ficha11($f);
        $d['hijos'] = hijos11($f['id']);
        $d['equivalencias'] = '/api/v1/convertir/cie11-a-cie10/' . ($f['codigo'] ?: $f['id']);
        responder($d);
    }
    if (preg_match('#^convertir/([^/]+)/(.+)$#', $r, $m)) {
        if (!isset($SENTIDOS[$m[1]])) throw new ErrorApi(404, 'sentido_invalido', 'El sentido es cie10-a-cie11 o cie11-a-cie10.');
        validar_codigo($m[2], $SENTIDOS[$m[1]]);
        $res = convertir($m[1], $m[2], !booleano_q('sin_sugerencias'));
        if ($res === null) throw new ErrorApi(404, 'codigo_inexistente', "El código {$SENTIDOS[$m[1]]} «{$m[2]}» no existe en la fuente.");
        responder($res);
    }
    throw new ErrorApi(404, 'ruta_inexistente', 'No existe esa ruta de la API. Ver /api/v1/openapi.json.');
}

function lote(array $SENTIDOS): void {
    global $T;
    $tipo = strtolower(trim(explode(';', $_SERVER['CONTENT_TYPE'] ?? $_SERVER['HTTP_CONTENT_TYPE'] ?? '')[0]));
    if ($tipo !== 'application/json' && !preg_match('#^application/.+\+json$#', $tipo))
        throw new ErrorApi(415, 'se_espera_json', 'El cuerpo tiene que ser JSON (Content-Type: application/json).');
    $crudo = (string)file_get_contents('php://input', false, null, 0, CUERPO_MAXIMO + 1);
    if (strlen($crudo) > CUERPO_MAXIMO) throw new ErrorApi(413, 'pedido_demasiado_grande', 'El cuerpo del pedido supera los 64 KB.');
    $cuerpo = json_decode($crudo, true);
    if (!is_array($cuerpo) || array_is_list_($cuerpo) && $cuerpo !== [])
        throw new ErrorApi(400, 'json_invalido', 'El cuerpo no es un objeto JSON válido.');
    if ($cuerpo === [] && trim($crudo) !== '{}') throw new ErrorApi(400, 'json_invalido', 'El cuerpo no es un objeto JSON válido.');
    $extra = array_diff(array_keys($cuerpo), ['sentido', 'codigos', 'sin_sugerencias']);
    if ($extra) {
        $extra = array_map('strval', $extra); sort($extra);
        throw new ErrorApi(400, 'campos_no_admitidos',
            'Sólo se aceptan «sentido», «codigos» y «sin_sugerencias». No envíe datos de pacientes.', array_slice($extra, 0, 10));
    }
    $sentido = $cuerpo['sentido'] ?? null;
    if (!is_string($sentido) || !isset($SENTIDOS[$sentido]))
        throw new ErrorApi(400, 'sentido_invalido', '«sentido» tiene que ser cie10-a-cie11 o cie11-a-cie10.');
    $codigos = $cuerpo['codigos'] ?? null;
    if (!is_array($codigos) || !$codigos || !array_is_list_($codigos))
        throw new ErrorApi(400, 'codigos_invalidos', '«codigos» tiene que ser una lista no vacía de códigos.');
    if (count($codigos) > LOTE_MAXIMO) throw new ErrorApi(413, 'lote_demasiado_grande', 'Hasta ' . LOTE_MAXIMO . ' códigos por pedido.');
    $sin_sug = array_key_exists('sin_sugerencias', $cuerpo) ? $cuerpo['sin_sugerencias'] : true;
    $resultados = []; $con_error = 0;
    foreach ($codigos as $c) {
        if (!is_string($c) || !preg_match('/^[A-Za-z0-9.\-\/&]{1,40}$/', trim($c))) {
            $resultados[] = ['codigo' => is_string($c) && mb_strlen($c) <= 40 ? $c : null,
                             'error' => ['codigo' => 'codigo_invalido', 'mensaje' => 'No tiene forma de código (no se aceptan textos libres).']];
            $con_error++;
            continue;
        }
        $r = convertir($sentido, trim($c), !$sin_sug);
        if ($r === null) {
            $resultados[] = ['codigo' => $c, 'error' => ['codigo' => 'codigo_inexistente', 'mensaje' => 'No existe en la fuente.']];
            $con_error++;
        } else {
            $resultados[] = ['codigo' => $c] + $r;
        }
    }
    responder(['sentido' => $sentido, 'total' => count($resultados), 'con_error' => $con_error,
               'resultados' => $resultados, 'aviso' => $T['aviso']]);
}

function array_is_list_(array $a): bool {
    $i = 0;
    foreach ($a as $k => $_) { if ($k !== $i++) return false; }
    return true;
}

cabeceras_comunes();
try {
    atender();
} catch (ErrorApi $e) {
    error_json($e->estado, $e->codigo, $e->getMessage(), $e->detalle);
} catch (Throwable $e) {
    error_json(500, 'error_interno', 'Error interno. Reintentar más tarde.');
}
