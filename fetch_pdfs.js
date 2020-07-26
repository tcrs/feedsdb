const puppeteer = require('puppeteer');
const fs = require('fs');

const maxConcurrent = 5;
const maxRetries = 3;

const path = process.argv[2];

var todo = JSON.parse(fs.readFileSync(path, 'utf8'));

async function getPdf(browser, spec) {
	const context = await browser.createIncognitoBrowserContext();
	const page = await context.newPage();

	await page.setJavaScriptEnabled(false);

	// Default 2 minute nav timeout
	page.setDefaultNavigationTimeout(2 * 60 * 1000);

	const url = spec.url;
	delete spec.url;
	console.log("Starting: ", url);

	if ('useragent' in spec) {
		await page.setUserAgent(spec.useragent);
		delete spec.useragent;
	}

	if ('viewport' in spec) {
		await page.setViewport(spec.viewport);
		delete spec.viewport;
	}

	var tries = 0;
	while (true) {
		try {
			await page.goto(url, {waitUntil: 'networkidle0'});
			break;
		}
		catch (e) {
			tries += 1;
			if (tries > maxRetries) {
				throw e;
			}
			else {
				console.log("Retry: ", url);
			}
		}
	}

	if ('css' in spec) {
		// page.addStyleTag will hang if the page has javascript disabled
		// It works by injecting a script similar to that below, but with a
		// promise that resolves on the style.onload callback - this never gets
		// called if javascript is disabled.
		// Here I'm assuming that the act of printing the PDF will make chromium
		// do a full relayout and so the injected style will take effect :s
		await page.evaluate(content => {
			const style = document.createElement('style');
			style.type = 'text/css';
			style.appendChild(document.createTextNode(content));
			document.head.appendChild(style);
		}, spec.css);
		delete spec.css;
	}

	if ('kill_sticky' in spec) {
		// Kill Sticky headers code from here:
		// https://alisdair.mcdiarmid.org/kill-sticky-headers/
		if (spec.kill_sticky) {
			await page.evaluate(() => {
				var i, elements = document.querySelectorAll('body *');

				for (i = 0; i < elements.length; i++) {
					if (getComputedStyle(elements[i]).position === 'fixed') {
						elements[i].parentNode.removeChild(elements[i]);
					}
				}
			});
		}
		delete spec.kill_sticky;
	}

	if ('mediafeatures' in spec) {
		await page.emulateMediaFeatures(spec.mediafeatures);
		delete spec.mediafeatures;
	}

	if ('mediatype' in spec) {
		await page.emulateMediaType(spec.mediatype);
		delete spec.mediatype;
	}

	await page.pdf(spec);

	console.log("Done: ", url)
}

(async () => {
	const browser = await puppeteer.launch();

	var running = []
	var all = []
	for (spec of todo) {
		const p = getPdf(browser, spec).catch((e) => { console.log(spec.url, e); });
		all.push(p);
		const e = p.then(() => running.splice(running.indexOf(e), 1));
		running.push(e);
		if (running.length >= maxConcurrent) {
			await Promise.race(running);
		}
	}

	await Promise.all(all);

	await browser.close();
})();
