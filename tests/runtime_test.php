<?php
declare(strict_types=1);
require __DIR__ . '/../docker/runtime.php';
$checks = 0;
function check(bool $condition, string $label): void
{
    global $checks;
    if (!$condition) {
        throw new RuntimeException('Test failed: ' . $label);
    }
    $checks++;
}
function refuses(callable $action, string $label): void
{
    try {
        $action();
    } catch (RuntimeSetupError $error) {
        check(true, $label);
        return;
    }
    check(false, $label);
}
foreach (['', '0', 'false', 'special!#;=', 'quoted"text', 'back\\slash', '$literal', '${HOME}'] as $value) {
    $parsed = parse_ini_string('value = ' . iniValue($value));
    check($parsed['value'] === $value, 'configuration value round trip');
}
refuses(fn() => iniValue("line\nbreak"), 'newline injection');
guardDatabaseVersion('6.0.8', '6.0.8');
check(true, 'same version accepted');
guardDatabaseVersion('5.5.0', '6.0.8');
check(true, 'older database may use authenticated wizard');
refuses(fn() => guardDatabaseVersion('6.1.0-dev', '6.0.8'), 'development downgrade refused');
refuses(fn() => guardDatabaseVersion('', '6.0.8'), 'unknown version refused');
$config = ['database' => ['host' => 'db', 'password' => 'test-only'],
           'plugins' => ['keep-setting' => '0']];
check(parse_ini_string(renderIni($config), true) === $config, 'configuration sections preserved');
$tmp = sys_get_temp_dir() . '/revive-permission-' . bin2hex(random_bytes(6));
mkdir($tmp, 0700);
try {
    $file = $tmp . '/README.txt';
    file_put_contents($file, 'packaged file');
    chmod($file, 0400);
    repairPath($file);
    clearstatcache();
    check(fileowner($file) === 33, 'packaged file repaired for PHP user');
    check((fileperms($file) & 0777) === 0640, 'files not world writable');
    symlink('/tmp', $tmp . '/link');
    refuses(fn() => repairPath($tmp . '/link'), 'symlink permission repair refused');
    unlink($tmp . '/link');
    unlink($file);
} finally {
    rmdir($tmp);
}
echo "Official-release runtime safety checks passed: {$checks}\n";