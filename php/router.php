<?php
// Router para probar la versión PHP con el servidor de desarrollo de PHP, que
// no lee .htaccess:   php -S 127.0.0.1:8000 -t dist dist/router.php
$ruta = (string)parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH);
$raiz = __DIR__;
if (preg_match('#/api/v1(/|$)#', $ruta)) { require "$raiz/api/v1/index.php"; return true; }
if (preg_match('#\.(db|sqlite)$#', $ruta) || str_ends_with($ruta, '/textos.json')) { http_response_code(403); return true; }
if (preg_match('#/api(/docs)?$#', $ruta)) { header("Location: $ruta/", true, 301); return true; }
if (preg_match('#/api/$#', $ruta)) { readfile("$raiz/api/index.html"); return true; }
if (preg_match('#/api/docs/$#', $ruta)) { readfile("$raiz/api/docs/index.html"); return true; }
return false;   // archivo estático tal cual
