<?php
declare(strict_types=1);

final class RuntimeSetupError extends RuntimeException {}
const REVIVE_ROOT = '/var/www/html/app';

function envRequired(string $key): string
{
    $value = getenv($key);
    if ($value === false || $value === '') {
        throw new RuntimeSetupError("Missing runtime environment variable: {$key}");
    }
    return $value;
}

function iniValue(string $value): string
{
    if (strpbrk($value, "\r\n\0") !== false) {
        throw new RuntimeSetupError('Configuration values must be single-line strings.');
    }
    $encoded = '"' . str_replace(['\\', '"', '$'], ['\\\\', '\\"', '\\$'], $value) . '"';
    $test = @parse_ini_string('value = ' . $encoded);
    if ($test === false || ($test['value'] ?? null) !== $value) {
        throw new RuntimeSetupError('A setting cannot be represented safely in the configuration.');
    }
    return $encoded;
}

function renderIni(array $config): string
{
    $output = ";<?php exit; ?>\n";
    foreach ($config as $section => $values) {
        if (!preg_match('/^[A-Za-z0-9_.-]+$/D', (string) $section) || !is_array($values)) {
            throw new RuntimeSetupError('Unsupported configuration structure.');
        }
        $output .= "\n[{$section}]\n";
        foreach ($values as $key => $value) {
            if (!preg_match('/^[A-Za-z0-9_.-]+$/D', (string) $key) || !is_scalar($value)) {
                throw new RuntimeSetupError('Unsupported configuration structure.');
            }
            $output .= $key . ' = ' . iniValue((string) $value) . "\n";
        }
    }
    return $output;
}

function publicHost(): string
{
    $host = getenv('REVIVE_HOST') ?: 'localhost:8080';
    if (!preg_match('/^(?:[A-Za-z0-9-]+\.)*[A-Za-z0-9-]+(?::[0-9]{1,5})?$/D', $host)) {
        throw new RuntimeSetupError('REVIVE_HOST must be a hostname, optionally with a port; no URL or path.');
    }
    return $host;
}

function publicScheme(): string
{
    $scheme = getenv('REVIVE_SCHEME') ?: 'http';
    if (!in_array($scheme, ['http', 'https'], true)) {
        throw new RuntimeSetupError('REVIVE_SCHEME must be http or https.');
    }
    return $scheme;
}

function dbEnvironment(): array
{
    $port = getenv('REVIVE_DB_PORT') ?: '3306';
    if (!ctype_digit($port) || (int) $port < 1 || (int) $port > 65535) {
        throw new RuntimeSetupError('Invalid database port.');
    }
    return [
        'type' => 'mysqli', 'protocol' => 'tcp', 'socket' => '', 'ssl' => '0',
        'host' => envRequired('REVIVE_DB_HOST'), 'port' => $port,
        'name' => envRequired('REVIVE_DB_NAME'), 'username' => envRequired('REVIVE_DB_USER'),
        'password' => envRequired('REVIVE_DB_PASSWORD'),
    ];
}

function connectDatabase(array $db): mysqli
{
    mysqli_report(MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT);
    try {
        $connection = mysqli_init();
        $connection->options(MYSQLI_OPT_CONNECT_TIMEOUT, 10);
        @$connection->real_connect($db['host'], $db['username'], $db['password'],
                                   $db['name'], (int) ($db['port'] ?: 3306));
        return $connection;
    } catch (mysqli_sql_exception $error) {
        throw new RuntimeSetupError('Database connection failed. Check hostname, credentials and network access; no credentials were logged.');
    }
}

function currentConfig(): ?array
{
    $var = REVIVE_ROOT . '/var';
    $name = explode(':', publicHost(), 2)[0];
    $path = "{$var}/{$name}.conf.php";
    if (!is_file($path) && is_file("{$var}/default.conf.php")) {
        $default = @parse_ini_file("{$var}/default.conf.php", true);
        if (isset($default['realConfig'])) {
            $name = (string) $default['realConfig'];
            if (!preg_match('/^[A-Za-z0-9_.-]+$/D', $name)) {
                throw new RuntimeSetupError('Invalid default configuration alias.');
            }
            $path = "{$var}/{$name}.conf.php";
        } else {
            $path = "{$var}/default.conf.php";
        }
    }
    if (!is_file($path)) {
        return null;
    }
    $config = @parse_ini_file($path, true);
    if ($config === false || !isset($config['database'], $config['table'])) {
        throw new RuntimeSetupError('Existing configuration is unreadable; it was not replaced.');
    }
    return $config;
}

function withRuntimeSettings(array $config): array
{
    if (getenv('REVIVE_DB_HOST') !== false && getenv('REVIVE_DB_HOST') !== '') {
        $config['database'] = array_replace($config['database'], dbEnvironment());
    }
    if (getenv('REVIVE_HOST') !== false && getenv('REVIVE_HOST') !== '') {
        foreach (['admin' => 'admin', 'delivery' => 'delivery', 'deliverySSL' => 'delivery',
                  'images' => 'images', 'imagesSSL' => 'images'] as $key => $path) {
            $config['webpath'][$key] = publicHost() . '/app/www/' . $path;
        }
        $config['openads']['requireSSL'] = publicScheme() === 'https' ? '1' : '0';
        $config['openads']['sslPort'] = '443';
        $config['cookie']['domain'] = '';
        $config['cookie']['viewerIdDomain'] = '';
        $config['cookie']['adminDomain'] = '';
    }
    $config['store']['webDir'] = REVIVE_ROOT . '/www/images';
    $config['delivery']['cachePath'] = '';
    return $config;
}

function writePrivate(string $path, string $contents): void
{
    $temporary = dirname($path) . '/.revive-tmp-' . bin2hex(random_bytes(8));
    if (file_put_contents($temporary, $contents, LOCK_EX) === false) {
        throw new RuntimeSetupError('Could not write persistent configuration.');
    }
    chmod($temporary, 0640);
    chown($temporary, 33);
    chgrp($temporary, 33);
    if (!rename($temporary, $path)) {
        @unlink($temporary);
        throw new RuntimeSetupError('Could not replace persistent configuration.');
    }
}

function saveConfig(array $config): void
{
    $host = explode(':', publicHost(), 2)[0];
    writePrivate(REVIVE_ROOT . "/var/{$host}.conf.php", renderIni($config));
    writePrivate(REVIVE_ROOT . '/var/default.conf.php',
                 ";<?php exit; ?>\nrealConfig = " . iniValue($host) . "\n");
}

function databaseVersion(array $config): string
{
    $prefix = (string) ($config['table']['prefix'] ?? '');
    if (!preg_match('/^[A-Za-z0-9_]*$/D', $prefix)) {
        throw new RuntimeSetupError('Invalid database table prefix.');
    }
    $db = connectDatabase($config['database']);
    try {
        $row = $db->query("SELECT value FROM `{$prefix}application_variable` WHERE name='oa_version'")->fetch_assoc();
    } catch (mysqli_sql_exception $error) {
        throw new RuntimeSetupError('Existing configuration does not point to a valid Revive database.');
    } finally {
        $db->close();
    }
    return (string) ($row['value'] ?? '');
}

function guardDatabaseVersion(string $actual, string $expected): void
{
    if ($actual === '' || version_compare($actual, $expected, '>')) {
        throw new RuntimeSetupError('The database is newer than or incompatible with this release. Downgrades are refused.');
    }
}

function repairPath(string $path): void
{
    if (is_link($path)) {
        throw new RuntimeSetupError('Linked mutable paths require manual review.');
    }
    if (!@chown($path, 33) || !@chgrp($path, 33) ||
        !@chmod($path, is_dir($path) ? 0750 : 0640)) {
        throw new RuntimeSetupError('Writable paths could not be repaired. Use read-write mounts and the default startup user.');
    }
}

function seedAndRepair(): void
{
    $targets = ['var' => REVIVE_ROOT . '/var', 'images' => REVIVE_ROOT . '/www/images',
                'plugins' => REVIVE_ROOT . '/plugins', 'admin-plugins' => REVIVE_ROOT . '/www/admin/plugins'];
    foreach ($targets as $part => $target) {
        if (is_link($target)) {
            throw new RuntimeSetupError('Linked volume roots require manual review.');
        }
        if (!is_dir($target)) {
            mkdir($target, 0750, true);
        }
        if (!is_file($target . '/.docker-seeded')) {
            $copy = proc_open(['cp', '-an', '/opt/revive-seed/' . $part . '/.', $target . '/'],
                             [0 => STDIN, 1 => STDOUT, 2 => STDERR], $pipes);
            if (!is_resource($copy) || proc_close($copy) !== 0) {
                throw new RuntimeSetupError('Could not seed packaged defaults.');
            }
            writePrivate($target . '/.docker-seeded', "Packaged defaults seeded once.\n");
        }
        repairPath($target);
    }
    // During installation Revive checks var recursively, including its packaged
    // top-level guard/UPGRADE files and this initialization lock.
    foreach (new DirectoryIterator('/opt/revive-seed/var') as $entry) {
        if ($entry->isFile() && !$entry->isLink()) {
            $path = REVIVE_ROOT . '/var/' . $entry->getFilename();
            if (is_file($path)) {
                repairPath($path);
            }
        }
    }
    repairPath(REVIVE_ROOT . '/var/.docker-init.lock');
    foreach (['cache', 'templates_compiled', 'tmp', 'plugins', 'plugins/cache',
              'plugins/config', 'plugins/log', 'plugins/recover', 'plugins/DataObjects'] as $relative) {
        $path = REVIVE_ROOT . '/var/' . $relative;
        if (is_link($path)) {
            throw new RuntimeSetupError('Linked mutable paths require manual review.');
        }
        if (!is_dir($path)) {
            mkdir($path, 0750, true);
        }
    }
    $recursive = [REVIVE_ROOT . '/var/cache', REVIVE_ROOT . '/var/templates_compiled',
                  REVIVE_ROOT . '/var/tmp', REVIVE_ROOT . '/var/plugins',
                  $targets['plugins'], $targets['admin-plugins'], $targets['images']];
    foreach ($recursive as $path) {
        repairPath($path);
        $iterator = new RecursiveIteratorIterator(
            new RecursiveDirectoryIterator($path, FilesystemIterator::SKIP_DOTS),
            RecursiveIteratorIterator::SELF_FIRST);
        foreach ($iterator as $entry) {
            repairPath($entry->getPathname());
        }
    }
    clearstatcache();
}

function installFresh(): void
{
    if (currentConfig() !== null) {
        throw new RuntimeSetupError('This instance already has configuration. No fresh installation was performed.');
    }
    $dbSettings = dbEnvironment();
    $connection = connectDatabase($dbSettings);
    $count = (int) $connection->query(
        'SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema=DATABASE()')->fetch_assoc()['n'];
    $connection->close();
    if ($count !== 0) {
        throw new RuntimeSetupError('Fresh installation requires an empty database. Restore configuration for existing data instead.');
    }
    $username = envRequired('REVIVE_ADMIN_USERNAME');
    $password = envRequired('REVIVE_ADMIN_PASSWORD');
    $email = envRequired('REVIVE_ADMIN_EMAIL');
    if (strlen($password) < 12 || !filter_var($email, FILTER_VALIDATE_EMAIL)) {
        throw new RuntimeSetupError('Use a valid administrator email and a password of at least 12 characters.');
    }
    $config = [
        'database' => $dbSettings,
        'table' => ['prefix' => 'rv_', 'type' => 'INNODB'],
        'admin' => ['username' => $username, 'password' => $password, 'email' => $email,
                    'language' => 'en', 'timezone' => 'UTC'],
        'paths' => ['requireSSL' => publicScheme() === 'https' ? '1' : '0',
                    'admin' => publicHost() . '/app/www/admin',
                    'delivery' => publicHost() . '/app/www/delivery',
                    'images' => publicHost() . '/app/www/images',
                    'imageStore' => REVIVE_ROOT . '/www/images'],
    ];
    $temporary = tempnam(sys_get_temp_dir(), 'revive-installer-');
    try {
        file_put_contents($temporary, renderIni($config));
        chmod($temporary, 0600);
        chown($temporary, 33);
        chgrp($temporary, 33);
        $child = proc_open(['gosu', 'www-data', 'php', 'scripts/installer', 'install',
                           $temporary, '--force'], [0 => STDIN, 1 => STDOUT, 2 => STDERR],
                          $pipes, REVIVE_ROOT);
        if (!is_resource($child) || proc_close($child) !== 0) {
            throw new RuntimeSetupError('Official installer failed. Inspect its output before retrying; partial data may exist.');
        }
    } finally {
        if ($temporary !== false) {
            @unlink($temporary);
        }
    }
    $installed = currentConfig();
    if ($installed === null || databaseVersion($installed) !== trim(file_get_contents('/opt/revive-version'))) {
        throw new RuntimeSetupError('Installation completion could not be verified.');
    }
    $installed['maintenance']['autoMaintenance'] = '0';
    saveConfig(withRuntimeSettings($installed));
    echo "Fresh installation completed; scheduled maintenance is still opt-in.\n";
}

function prepare(): void
{
    $var = REVIVE_ROOT . '/var';
    if (is_link($var)) {
        throw new RuntimeSetupError('Linked volume roots require manual review.');
    }
    if (!is_dir($var)) {
        mkdir($var, 0750, true);
    }
    $lock = fopen($var . '/.docker-init.lock', 'c');
    if ($lock === false || !flock($lock, LOCK_EX)) {
        throw new RuntimeSetupError('Could not lock initialization.');
    }
    try {
        if (is_file($var . '/.docker-commit') || is_file($var . '/.revive-bootstrap.json')) {
            throw new RuntimeSetupError('Development/private-bootstrap state is not supported here. Use a separate deployment; do not downgrade.');
        }
        $release = trim(file_get_contents('/opt/revive-release'));
        $marker = $var . '/.docker-release';
        if (is_file($marker) && trim(file_get_contents($marker)) !== $release) {
            throw new RuntimeSetupError('Release mismatch. Back up and prepare a controlled upgrade; do not overwrite installed plugin volumes.');
        }
        seedAndRepair();
        $config = currentConfig();
        if ($config === null && getenv('REVIVE_AUTO_INSTALL') === '1') {
            installFresh();
            $config = currentConfig();
        }
        if ($config !== null) {
            $config = withRuntimeSettings($config);
            $version = databaseVersion($config);
            guardDatabaseVersion($version, trim(file_get_contents('/opt/revive-version')));
            saveConfig($config);
            if ($version !== trim(file_get_contents('/opt/revive-version'))) {
                echo "Older database detected: complete the authenticated Revive upgrade manually.\n";
            }
        }
        writePrivate($marker, $release . "\n");
    } finally {
        flock($lock, LOCK_UN);
        fclose($lock);
    }
}

function enableMaintenance(): void
{
    $config = currentConfig();
    if ($config === null || databaseVersion($config) !== trim(file_get_contents('/opt/revive-version'))) {
        throw new RuntimeSetupError('Complete installation/upgrade before enabling maintenance.');
    }
    $config['maintenance']['autoMaintenance'] = '0';
    saveConfig($config);
    writePrivate(REVIVE_ROOT . '/var/.maintenance-enabled', "Enabled explicitly.\n");
    echo "Request-triggered maintenance disabled; hourly worker enabled.\n";
}

if (realpath($_SERVER['SCRIPT_FILENAME'] ?? '') === __FILE__) {
    umask(0077);
    try {
        match ($argv[1] ?? 'prepare') {
            'prepare' => prepare(),
            'install' => installFresh(),
            'enable-maintenance' => enableMaintenance(),
            default => throw new RuntimeSetupError('Unknown runtime action.'),
        };
    } catch (RuntimeSetupError $error) {
        fwrite(STDERR, 'Revive setup stopped: ' . $error->getMessage() . "\n");
        exit(1);
    } catch (Throwable $error) {
        fwrite(STDERR, "Revive setup stopped unexpectedly; no credentials were logged.\n");
        exit(1);
    }
}