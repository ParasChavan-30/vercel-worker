<?php
/**
 * api.php — Server Bridge Endpoint for COA Address Scraper
 * 
 * Allows local scrapers to fetch pending registration IDs and send back
 * scraped address data via HTTP requests, interacting directly with MySQL on the server.
 */

// Enable error reporting for debugging (disable in production if needed)
error_reporting(E_ALL);
ini_set('display_errors', 1);

header('Content-Type: application/json; charset=utf-8');

// ─────────────────────────────────────────────────────────
// Configuration
// ─────────────────────────────────────────────────────────
$DB_HOST     = getenv('DB_HOST') ?: 'localhost';
$DB_PORT     = getenv('DB_PORT') ?: '3306';
$DB_NAME     = getenv('DB_NAME') ?: 'paras_coa_scraper';
$DB_USER     = getenv('DB_USER') ?: 'paras_paras_mymysqluser';
$DB_PASS     = getenv('DB_PASSWORD') ?: 'Student@123'; // Fill in your server database password here
$API_SECRET  = getenv('REMOTE_API_KEY') ?: 'coa_secret_api_key_2026';

// Allow testing via PHP CLI: php -f api.php "action=get_total_pending&api_key=coa_secret_api_key_2026"
if (php_sapi_name() === 'cli' && isset($argv[1])) {
    parse_str($argv[1], $_REQUEST);
}

// Polyfill getallheaders for non-Apache / CGI / CLI environments
if (!function_exists('getallheaders')) {
    function getallheaders() {
        $headers = [];
        foreach ($_SERVER as $name => $value) {
            if (substr($name, 0, 5) === 'HTTP_') {
                $header_name = str_replace(' ', '-', ucwords(strtolower(str_replace('_', ' ', substr($name, 5)))));
                $headers[$header_name] = $value;
            }
        }
        return $headers;
    }
}

// ─────────────────────────────────────────────────────────
// API Key Authentication
// ─────────────────────────────────────────────────────────
$headers = getallheaders();
$provided_key = '';

if (isset($headers['X-API-KEY'])) {
    $provided_key = $headers['X-API-KEY'];
} elseif (isset($headers['x-api-key'])) {
    $provided_key = $headers['x-api-key'];
} elseif (isset($_REQUEST['api_key'])) {
    $provided_key = $_REQUEST['api_key'];
}

if ($API_SECRET !== '' && $provided_key !== $API_SECRET) {
    http_response_code(401);
    echo json_encode([
        'status'  => 'error',
        'message' => 'Unauthorized: Invalid or missing API key'
    ]);
    exit;
}

// ─────────────────────────────────────────────────────────
// Database Connection (PDO)
// ─────────────────────────────────────────────────────────
try {
    $dsn = "mysql:host={$DB_HOST};port={$DB_PORT};dbname={$DB_NAME};charset=utf8mb4";
    $pdo = new PDO($dsn, $DB_USER, $DB_PASS, [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES   => false,
    ]);
} catch (PDOException $e) {
    http_response_code(500);
    echo json_encode([
        'status'  => 'error',
        'message' => 'Database connection failed: ' . $e->getMessage()
    ]);
    exit;
}

// ─────────────────────────────────────────────────────────
// Parse Request & Actions
// ─────────────────────────────────────────────────────────
$raw_input = file_get_contents('php://input');
$json_input = json_decode($raw_input, true) ?: [];

$action = $_REQUEST['action'] ?? $json_input['action'] ?? '';

try {
    switch ($action) {

        // 1. Fetch pending records needing an address
        case 'get_pending':
            $limit = isset($_REQUEST['limit']) ? (int)$_REQUEST['limit'] : ($json_input['limit'] ?? 200);
            if ($limit <= 0) $limit = 200;

            $where = ["(address IS NULL OR address = '' OR address = '—')"];
            $params = [];

            $start_year = isset($_REQUEST['start_year']) ? (int)$_REQUEST['start_year'] : ($json_input['start_year'] ?? null);
            $end_year   = isset($_REQUEST['end_year']) ? (int)$_REQUEST['end_year'] : ($json_input['end_year'] ?? null);

            if ($start_year) {
                $where[] = "year >= :start_year";
                $params[':start_year'] = $start_year;
            }
            if ($end_year) {
                $where[] = "year <= :end_year";
                $params[':end_year'] = $end_year;
            }

            $whereSql = implode(' AND ', $where);
            $query = "
                SELECT id, registration_no
                FROM coa_registrations
                WHERE {$whereSql}
                ORDER BY year ASC, serial ASC
                LIMIT {$limit}
            ";
            $stmt = $pdo->prepare($query);
            $stmt->execute($params);
            $rows = $stmt->fetchAll();

            echo json_encode([
                'status' => 'success',
                'count'  => count($rows),
                'data'   => $rows
            ]);
            break;

        // 2. Fetch total pending records count
        case 'get_total_pending':
            $where = ["(address IS NULL OR address = '' OR address = '—')"];
            $params = [];

            $start_year = isset($_REQUEST['start_year']) ? (int)$_REQUEST['start_year'] : ($json_input['start_year'] ?? null);
            $end_year   = isset($_REQUEST['end_year']) ? (int)$_REQUEST['end_year'] : ($json_input['end_year'] ?? null);

            if ($start_year) {
                $where[] = "year >= :start_year";
                $params[':start_year'] = $start_year;
            }
            if ($end_year) {
                $where[] = "year <= :end_year";
                $params[':end_year'] = $end_year;
            }

            $whereSql = implode(' AND ', $where);
            $query = "
                SELECT COUNT(*) as total
                FROM coa_registrations
                WHERE {$whereSql}
            ";
            $stmt = $pdo->prepare($query);
            $stmt->execute($params);
            $row = $stmt->fetch();

            echo json_encode([
                'status'        => 'success',
                'total_pending' => (int)$row['total']
            ]);
            break;

        // 3. Update single address
        case 'update_address':
            $registration_no = $json_input['registration_no'] ?? $_REQUEST['registration_no'] ?? null;
            $address         = $json_input['address'] ?? $_REQUEST['address'] ?? null;

            if (empty($registration_no) || $address === null) {
                http_response_code(400);
                echo json_encode([
                    'status'  => 'error',
                    'message' => 'Missing registration_no or address parameter'
                ]);
                exit;
            }

            $query = "UPDATE coa_registrations SET address = :address WHERE registration_no = :reg_no";
            $stmt = $pdo->prepare($query);
            $stmt->execute([
                ':address' => $address,
                ':reg_no'  => $registration_no
            ]);

            $affected = $stmt->rowCount();
            echo json_encode([
                'status'   => 'success',
                'updated'  => $affected > 0,
                'affected' => $affected
            ]);
            break;

        // 3b. Batch update multiple addresses in a single transaction
        case 'update_addresses_batch':
            $updates = $json_input['updates'] ?? [];
            if (!is_array($updates) || empty($updates)) {
                echo json_encode([
                    'status'  => 'success',
                    'updated' => 0,
                    'message' => 'No updates provided'
                ]);
                exit;
            }

            $pdo->beginTransaction();
            $stmt = $pdo->prepare("UPDATE coa_registrations SET address = :address WHERE registration_no = :reg_no");
            $updated_count = 0;

            foreach ($updates as $item) {
                $reg_no  = $item['registration_no'] ?? null;
                $address = $item['address'] ?? null;
                if (!empty($reg_no) && $address !== null) {
                    $stmt->execute([
                        ':address' => $address,
                        ':reg_no'  => $reg_no
                    ]);
                    $updated_count += $stmt->rowCount();
                }
            }

            $pdo->commit();
            echo json_encode([
                'status'   => 'success',
                'updated'  => $updated_count
            ]);
            break;

        // 4. Fetch total CAPTCHA failed records count
        case 'get_total_failed':
            $query = "
                SELECT COUNT(*) as total
                FROM coa_registrations
                WHERE address = 'CAPTCHA_FAILED'
            ";
            $stmt = $pdo->query($query);
            $row = $stmt->fetch();

            echo json_encode([
                'status'       => 'success',
                'total_failed' => (int)$row['total']
            ]);
            break;

        // 5. Reset CAPTCHA_FAILED records back to NULL for retry pass
        case 'reset_failed':
            $query = "UPDATE coa_registrations SET address = NULL WHERE address = 'CAPTCHA_FAILED'";
            $stmt = $pdo->query($query);
            $affected = $stmt->rowCount();

            echo json_encode([
                'status'      => 'success',
                'reset_count' => $affected
            ]);
            break;

        default:
            http_response_code(400);
            echo json_encode([
                'status'  => 'error',
                'message' => 'Invalid action. Supported actions: get_pending, get_total_pending, update_address, get_total_failed, reset_failed'
            ]);
            break;
    }
} catch (Throwable $e) {
    http_response_code(500);
    echo json_encode([
        'status'  => 'error',
        'message' => 'Execution error: ' . $e->getMessage()
    ]);
}
