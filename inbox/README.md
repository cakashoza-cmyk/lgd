# GJEPC report inbox

GJEPC's website refuses GitHub's servers (HTTP 403), so the daily job cannot always download new reports itself.
When GJEPC publishes a new monthly export review (around the 7th-15th), download the PDF from
https://gjepc.org/statistics.php ("Gem & Jewellery Exports ... Master Report for the Month of ...") and upload it here
(GitHub: Add file → Upload files). The workflow runs on upload and reads only the PDF's text, exactly like a direct download.
