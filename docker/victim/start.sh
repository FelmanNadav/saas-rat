#!/bin/bash
set -e

# SSH
service ssh start

# vsftpd
service vsftpd start

# Apache
service apache2 start

# MySQL — start, then configure root with empty password
service mysql start
sleep 3

# Ubuntu 20.04 ships MySQL 8.0: root uses auth_socket by default.
# Switch to native password with no password (classic Metasploitable2 behaviour).
mysql -u root -e "
    ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '';
    FLUSH PRIVILEGES;
" 2>/dev/null || true

# Seed a demo database
mysql -u root -e "
    CREATE DATABASE IF NOT EXISTS webapp;
    USE webapp;
    CREATE TABLE IF NOT EXISTS users (
        id   INT PRIMARY KEY AUTO_INCREMENT,
        username VARCHAR(50),
        password VARCHAR(100),
        role VARCHAR(20)
    );
    INSERT IGNORE INTO users (id, username, password, role) VALUES
        (1, 'admin', '21232f297a57a5a743894a0e4a801fc3', 'admin'),
        (2, 'alice', '5f4dcc3b5aa765d61d8327deb882cf99', 'user'),
        (3, 'bob',   '5f4dcc3b5aa765d61d8327deb882cf99', 'user');
" 2>/dev/null || true

# Samba
service smbd start
service nmbd start

echo "[victim] ssh(22) ftp(21) http(80) mysql(3306) smb(445) — all up"
echo "[victim] Starting C2 client (CLIENT_ID=${CLIENT_ID})"

exec python3 -u /app/client.py
