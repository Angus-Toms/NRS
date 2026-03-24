/**
 * PTD static asset worker
 *
 * Proxies the R2 bucket at static.protridata.com.
 * For missing athlete images, returns the default avatar so the browser
 * never gets a text/html 404 for an <img> request (avoids CORB console spam).
 */

const DEFAULT_AVATAR_KEY = "imgs/default_user_64.webp";
const ATHLETE_IMG_PREFIX = "athlete_imgs/";

const CACHE_TTL = {
    imgs:         31536000,   // 1 year — hashed filenames
    athlete_imgs: 86400,      // 1 day  — updated periodically
    css:          604800,     // 1 week
    js:           604800,
    default:      86400,
};

function cacheTtl(key) {
    const dir = key.split("/")[0];
    return CACHE_TTL[dir] ?? CACHE_TTL.default;
}

export default {
    async fetch(request, env) {
        const url = new URL(request.url);
        // Strip leading slash to get the R2 key
        const key = url.pathname.replace(/^\//, "");

        // Only handle GET/HEAD
        if (request.method !== "GET" && request.method !== "HEAD") {
            return new Response("Method not allowed", { status: 405 });
        }

        let object = await env.BUCKET.get(key);

        // Missing athlete image → silently serve the default avatar
        if (object === null && key.startsWith(ATHLETE_IMG_PREFIX)) {
            object = await env.BUCKET.get(DEFAULT_AVATAR_KEY);
        }

        if (object === null) {
            return new Response("Not Found", { status: 404 });
        }

        const headers = new Headers();
        object.writeHttpMetadata(headers);
        headers.set("Cache-Control", `public, max-age=${cacheTtl(key)}`);
        // Ensure images always get the right content type (R2 may omit it)
        if (!headers.has("Content-Type")) {
            if (key.endsWith(".webp")) headers.set("Content-Type", "image/webp");
            else if (key.endsWith(".png"))  headers.set("Content-Type", "image/png");
            else if (key.endsWith(".css"))  headers.set("Content-Type", "text/css");
            else if (key.endsWith(".js"))   headers.set("Content-Type", "application/javascript");
        }

        return new Response(object.body, { headers });
    },
};
