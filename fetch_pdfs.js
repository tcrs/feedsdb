const puppeteer = require('puppeteer');
const fs = require('fs');

const maxConcurrent = 5;

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

	await page.goto(url, {waitUntil: 'networkidle0'});
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
