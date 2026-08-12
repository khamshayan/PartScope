import { MongoClient } from 'mongodb';

import { config } from '../config.js';

let client = null;

export async function getMongo() {
  if (!client) {
    client = new MongoClient(config.mongo.uri, { serverSelectionTimeoutMS: 5000 });
    await client.connect();
  }
  return client.db(config.mongo.database);
}

export async function getPartsCollection() {
  const db = await getMongo();
  return db.collection('parts');
}

/**
 * Catalog detail for a set of MPNs, keyed by MPN.
 * One query for the whole batch; an RFQ is a batch of lines, and fetching each
 * line's catalog record separately turns one round trip into twenty.
 */
export async function findPartsByMpn(mpns) {
  if (!mpns.length) return new Map();
  const parts = await getPartsCollection();
  const docs = await parts
    .find({ mpn: { $in: mpns } }, { projection: { _id: 0 } })
    .toArray();
  return new Map(docs.map((doc) => [doc.mpn, doc]));
}

export async function findPartByMpn(mpn) {
  const parts = await getPartsCollection();
  return parts.findOne({ mpn }, { projection: { _id: 0 } });
}

export async function pingMongo() {
  const db = await getMongo();
  await db.command({ ping: 1 });
  return true;
}

export async function closeMongo() {
  if (client) {
    await client.close();
    client = null;
  }
}
