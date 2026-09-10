/**
 * Canada LMIA Scraper - Google Sheet Auto-Sync Script
 * 
 * SETUP INSTRUCTIONS:
 * 1. Open your Google Sheet.
 * 2. Click Extensions > Apps Script.
 * 3. Delete any code and paste this entire file.
 * 4. Click Deploy > New deployment.
 * 5. Select type: 'Web app'.
 * 6. Set 'Execute as': 'Me', 'Who has access': 'Anyone'.
 * 7. Click Deploy, copy the Web App URL, and paste it into Apify as 'google_sheet_webhook_url'!
 */

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    var tabName = data.tab_name || "Today_LMIA_Jobs";
    var headers = data.headers || [];
    var rows = data.rows || [];
    
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = ss.getSheetByName(tabName);
    
    // Create tab if it doesn't exist
    if (!sheet) {
      sheet = ss.insertSheet(tabName);
    }
    
    // Clear yesterday's data completely
    sheet.clear();
    
    // Write headers and today's new rows
    if (headers.length > 0 && rows.length > 0) {
      var allData = [headers].concat(rows);
      sheet.getRange(1, 1, allData.length, headers.length).setValues(allData);
      
      // Format headers
      sheet.getRange(1, 1, 1, headers.length).setFontWeight("bold").setBackground("#e6f4ea");
      sheet.setFrozenRows(1);
    } else if (headers.length > 0) {
      sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
      sheet.getRange(1, 1, 1, headers.length).setFontWeight("bold").setBackground("#e6f4ea");
    }
    
    return ContentService.createTextOutput(JSON.stringify({
      status: "success",
      message: "Overwrote " + tabName + " with " + rows.length + " fresh records for today.",
      count: rows.length
    })).setMimeType(ContentService.MimeType.JSON);
    
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({
      status: "error",
      message: err.toString()
    })).setMimeType(ContentService.MimeType.JSON);
  }
}
