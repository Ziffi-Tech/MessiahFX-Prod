#!/usr/bin/env node
// Generate a scrypt password hash for DASHBOARD_USERS.
//
//   node scripts/hash-password.mjs 'my secret password'
//
// Prints: scrypt$<saltHex>$<hashHex>
// Paste that as the "password" field of a DASHBOARD_USERS entry, e.g.
//   DASHBOARD_USERS='[{"username":"alice","password":"scrypt$..","role":"admin"}]'

import { scryptSync, randomBytes } from "node:crypto";

const pw = process.argv[2];
if (!pw) {
  console.error("usage: node scripts/hash-password.mjs <password>");
  process.exit(1);
}

const salt = randomBytes(16);
const hash = scryptSync(pw, salt, 64);
console.log(`scrypt$${salt.toString("hex")}$${hash.toString("hex")}`);
