from flask import Flask, render_template, request, redirect, url_for, jsonify, session
import os
from supabase import create_client
from datetime import datetime
from dotenv import load_dotenv
from io import BytesIO
from flask import send_file

try:
    import pandas as pd
    
    # Check if xlsxwriter is available
    EXCEL_ENGINE = None
    try:
        import xlsxwriter
        EXCEL_ENGINE = 'xlsxwriter'
    except ImportError:
        pass
            
except ImportError:
    pd = None
    EXCEL_ENGINE = None

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

SUPABASE_URL = os.environ.get('SUPA_URL')
SUPABASE_KEY = os.environ.get('SUPA_KEY')

# Default value for bookings status
BOOKINGS_CLOSED = os.environ.get('BOOKINGS_CLOSED', 'false').lower() == 'true'

# Google API configuration
CLIENT_SECRETS_FILE = os.environ.get('GOOGLE_CLIENT_SECRETS_FILE', 'client_secret.json')
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
API_SERVICE_NAME = 'sheets'
API_VERSION = 'v4'
REDIRECT_URI = os.environ.get('REDIRECT_URI', 'http://localhost:5000/oauth2callback')

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialize cart in session
@app.before_request
def before_request():
    if 'cart' not in session:
        session['cart'] = {}

@app.route('/')
def home():
    if BOOKINGS_CLOSED:
        return redirect(url_for('bookings_closed'))
        
    # Get food items from Supabase
    response = supabase.table('food-items').select('*').order('id').execute()
    food_items = response.data
    
    # Calculate ordered quantities from order-list
    try:
        orders_response = supabase.table('order-list').select('*').execute()
        orders = orders_response.data
        
        ordered_quantities = {}
        
        for order in orders:
            if isinstance(order, dict):
                if 'item' in order and isinstance(order['item'], str) and '(x' in order['item']:
                    items_string = order['item']
                    items_parts = items_string.split(', ')
                    
                    for item_part in items_parts:
                        if '(x' in item_part:
                            item_name_parts = item_part.split(' (x')
                            if len(item_name_parts) >= 2:
                                item_name = item_name_parts[0]
                                try:
                                    quantity_str = item_name_parts[1].rstrip(')')
                                    quantity = int(quantity_str)
                                except (ValueError, IndexError):
                                    quantity = 1
                                
                                # Update ordered quantities
                                if item_name in ordered_quantities:
                                    ordered_quantities[item_name] += quantity
                                else:
                                    ordered_quantities[item_name] = quantity
                
                elif 'item' in order and not isinstance(order['item'], list):
                    item_name = str(order['item'])
                    try:
                        quantity = int(order.get('quantity', 1))
                    except (ValueError, TypeError):
                        quantity = 1
                    
                    if item_name in ordered_quantities:
                        ordered_quantities[item_name] += quantity
                    else:
                        ordered_quantities[item_name] = quantity
        
        for item in food_items:
            item_name = item['name']
            inventory_quantity = item.get('quantity', 0)
            ordered = ordered_quantities.get(item_name, 0)
            remaining = inventory_quantity - ordered
            item['remaining'] = remaining
            item['out_of_stock'] = remaining <= 0 # Mark as out of stock if remaining is less than or equal to 0
        
    except Exception as e:
        print(f"Error calculating stock status: {str(e)}")
        for item in food_items:
            item['out_of_stock'] = False
    
    # Sale date
    sale_date = "May 31, 2025"
    
    return render_template('home.html', food_items=food_items, sale_date=sale_date)

@app.route('/add_to_cart', methods=['POST'])
def add_to_cart():
    data = request.json
    item_id = data.get('item_id')
    requested_quantity = data.get('quantity', 1)
    
    try:
        response = supabase.table('food-items').select('*').eq('id', item_id).execute()
        print(f"Response from Supabase for item_id {item_id}: {response}")
        item = response.data[0] if response.data else None
        
        if item:
            # Check stock availability
            item_name = item['name']
            inventory_quantity = item.get('quantity', 0)
            
            # Get all orders to calculate current ordered quantity
            orders_response = supabase.table('order-list').select('*').execute()
            orders = orders_response.data
            
            # Calculate already ordered quantity
            ordered_quantity = 0
            for order in orders:
                if isinstance(order, dict) and 'item' in order and isinstance(order['item'], str):
                    if item_name in order['item']:
                        # Try to extract the specific quantity for this item
                        if f"{item_name} (x" in order['item']:
                            items_string = order['item']
                            items_parts = items_string.split(', ')
                            
                            for item_part in items_parts:
                                if item_name in item_part and '(x' in item_part:
                                    item_name_parts = item_part.split(' (x')
                                    if len(item_name_parts) >= 2 and item_name_parts[0] == item_name:
                                        try:
                                            quantity_str = item_name_parts[1].rstrip(')')
                                            ordered_quantity += int(quantity_str)
                                        except (ValueError, IndexError):
                                            pass
            
            if item_id in session['cart']:
                current_cart_quantity = session['cart'][item_id]['quantity']
            else:
                current_cart_quantity = 0
            
            available_stock = inventory_quantity - ordered_quantity
            
            can_add = available_stock - current_cart_quantity
            
            if can_add <= 0:
                return jsonify({
                    'success': False, 
                    'error': 'This item is out of stock.'
                })
            
            actual_quantity_to_add = min(requested_quantity, can_add)
            
            if item_id in session['cart']:
                session['cart'][item_id]['quantity'] += actual_quantity_to_add
            else:
                session['cart'][item_id] = {
                    'name': item['name'],
                    'price': item['price'],
                    'quantity': actual_quantity_to_add,
                    'image': item['image']
                }
            session.modified = True
            
            if actual_quantity_to_add < requested_quantity:
                return jsonify({
                    'success': True, 
                    'cart_size': len(session['cart']),
                    'adjusted': True,
                    'message': f'Only {actual_quantity_to_add} items were added due to stock limitations.'
                })
            else:
                return jsonify({'success': True, 'cart_size': len(session['cart'])})
        else:
            print(f"Item with ID {item_id} not found in database")
            return jsonify({'success': False, 'error': 'Item not found'})
    except Exception as e:
        print(f"Error fetching item from Supabase: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/get_cart')
def get_cart():
    return jsonify(session['cart'])

@app.route('/update_cart', methods=['POST'])
def update_cart():
    data = request.json
    item_id = data.get('item_id')
    quantity = data.get('quantity', 0)
    
    try:
        if item_id in session['cart']:
            if quantity <= 0:
                session['cart'].pop(item_id)
            else:
                session['cart'][item_id]['quantity'] = quantity
            session.modified = True
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Item not found in cart'})
    except Exception as e:
        print(f"Error updating cart: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/checkout')
def checkout():
    # Before displaying checkout, verify that all items are still in stock in sufficient quantities
    try:
        cart = session.get('cart', {})
        
        # Get all active orders to calculate current stock levels
        orders_response = supabase.table('order-list').select('*').execute()
        orders = orders_response.data
        
        # Calculate ordered quantities for each item
        ordered_quantities = {}
        for order in orders:
            if isinstance(order, dict) and 'item' in order and isinstance(order['item'], str) and '(x' in order['item']:
                items_string = order['item']
                items_parts = items_string.split(', ')
                
                for item_part in items_parts:
                    if '(x' in item_part:
                        item_name_parts = item_part.split(' (x')
                        if len(item_name_parts) >= 2:
                            item_name = item_name_parts[0]
                            try:
                                quantity_str = item_name_parts[1].rstrip(')')
                                quantity = int(quantity_str)
                                
                                if item_name in ordered_quantities:
                                    ordered_quantities[item_name] += quantity
                                else:
                                    ordered_quantities[item_name] = quantity
                            except (ValueError, IndexError):
                                pass
        
        # Check if any cart item exceeds available stock
        cart_modified = False
        items_updated = []
        
        for item_id, item_details in list(cart.items()):
            item_response = supabase.table('food-items').select('*').eq('id', item_id).execute()
            if item_response.data:
                item = item_response.data[0]
                inventory_quantity = item.get('quantity', 0)
                item_name = item['name']
                
                ordered_quantity = ordered_quantities.get(item_name, 0)
                
                max_allowed = inventory_quantity - ordered_quantity
                
                # If requested quantity exceeds maximum allowed, adjust it
                if item_details['quantity'] > max_allowed:
                    if max_allowed <= 0:
                        session['cart'].pop(item_id)
                        items_updated.append(f"{item_name} (removed - out of stock)")
                    else:
                        # Reduce quantity to maximum allowed
                        session['cart'][item_id]['quantity'] = max_allowed
                        items_updated.append(f"{item_name} (adjusted to {max_allowed})")
                    
                    cart_modified = True
        
        if cart_modified:
            session.modified = True
            flash_message = "Some items in your cart were adjusted due to stock limitations: " + ", ".join(items_updated)
            return render_template('checkout.html', cart_adjusted=True, adjustment_message=flash_message)
    
    except Exception as e:
        print(f"Error verifying cart: {str(e)}")
    
    return render_template('checkout.html')

@app.route('/place_order', methods=['POST'])
def place_order():
    try:
        customer_name = request.form.get('name')
        phone = request.form.get('phone')
        membership = request.form.get('membership', '')
        
        order_id = int(datetime.now().timestamp())
        
        print(f"Generated order ID: {order_id}")
        print(f"Cart contents: {session.get('cart', {})}")
        
        if not session.get('cart'):
            return render_template('error.html', error="Your cart is empty. Please add items before checkout.")
        
        try:
            all_items = []
            total_quantity = 0
            
            for item_id, item_data in session['cart'].items():
                all_items.append(f"{item_data['name']} (x{item_data['quantity']})")
                total_quantity += item_data['quantity']
            
            items_text = ", ".join(all_items)
            
            order_data = {
                'order_id': order_id,
                'customer_name': customer_name,
                'item': items_text,
                'quantity': total_quantity,
            }
            
            try:
                order_data['phone'] = int(phone) if phone and phone.isdigit() else 0
            except (ValueError, TypeError):
                order_data['phone'] = 0
            
            try:
                order_data['membership'] = int(membership) if membership and membership.isdigit() else 0
            except (ValueError, TypeError):
                order_data['membership'] = 0
            
            print(f"Inserting order with consolidated data: {order_data}")
            
            # Insert the order into the database
            insert_response = supabase.table('order-list').insert(order_data).execute()
            print(f"Insert response: {insert_response}")
            
            if hasattr(insert_response, 'data') and insert_response.data:
                # Clear the cart on successful order
                session['cart'] = {}
                print("Order placed successfully with JSON arrays")
                return render_template('order_confirmation.html', order_id=order_id)
            else:
                print("Insert may have failed but did not throw an exception")
                return render_template('error.html', 
                                     error=f"Failed to place your order. Please try again or contact support.",
                                     details="Could not add your order to the database.")
                
        except Exception as e:
            print(f"Error inserting order: {str(e)}")
            return render_template('error.html', 
                                 error=f"Failed to place your order: {str(e)}",
                                 details="There was an error processing your request.")
            
    except Exception as e:
        import traceback
        print(f"Error placing order: {str(e)}")
        print(traceback.format_exc())
        return render_template('error.html', error=f"Order processing error: {str(e)}")

@app.route('/admin')
def admin():
    try:
        # Get all active orders and sort by order_id
        response = supabase.table('order-list').select('*').order('order_id', desc=True).execute()
        orders = response.data
        
        item_summary = {}
        total_amount_collected = 0
        
        food_items_response = supabase.table('food-items').select('*').execute()
        food_items = food_items_response.data
        
        price_lookup = {}
        for item in food_items:
            price_lookup[item['name']] = item.get('price', 0)
        
        # Process orders
        for order in orders:
            if isinstance(order, dict):
                order_total = 0
                
                if 'item' in order and isinstance(order['item'], str) and '(x' in order['item']:
                    items_string = order['item']
                    items_parts = items_string.split(', ')
                    
                    for item_part in items_parts:
                        if '(x' in item_part:
                            item_name_parts = item_part.split(' (x')
                            if len(item_name_parts) >= 2:
                                item_name = item_name_parts[0]
                                try:
                                    quantity_str = item_name_parts[1].rstrip(')')
                                    quantity = int(quantity_str)
                                except (ValueError, IndexError):
                                    quantity = 1
                                
                                # Update order total
                                item_price = price_lookup.get(item_name, 0)
                                order_total += item_price * quantity
                                
                                if item_name in item_summary:
                                    item_summary[item_name] += quantity
                                else:
                                    item_summary[item_name] = quantity
                
                elif 'item' in order and not isinstance(order['item'], list):
                    item_name = str(order['item'])
                    try:
                        quantity = int(order.get('quantity', 1))
                    except (ValueError, TypeError):
                        quantity = 1
                    
                    # Update order total
                    item_price = price_lookup.get(item_name, 0)
                    order_total += item_price * quantity
                        
                    # Update summary
                    if item_name in item_summary:
                        item_summary[item_name] += quantity
                    else:
                        item_summary[item_name] = quantity
                
                # Add total amount to the order
                order['total_amount'] = round(order_total, 3)
                total_amount_collected += order_total
        
        sorted_summary = dict(sorted(item_summary.items()))
        
        parsed_orders = []
        for order in orders:
            if isinstance(order, dict):
                parsed_order = dict(order)  # Make a copy
                
                if 'item' in parsed_order and isinstance(parsed_order['item'], str) and '(x' in parsed_order['item']:
                    items_string = parsed_order['item']
                    items_parts = items_string.split(', ')
                    
                    parsed_items = []
                    for item_part in items_parts:
                        if '(x' in item_part:
                            name_parts = item_part.split(' (x')
                            if len(name_parts) >= 2:
                                name = name_parts[0]
                                qty = name_parts[1].rstrip(')')
                                parsed_items.append({'name': name, 'quantity': qty})
                    
                    parsed_order['parsed_items'] = parsed_items
                
                parsed_orders.append(parsed_order)
        
        return render_template('admin.html', 
                              orders=parsed_orders, 
                              item_summary=sorted_summary,
                              total_amount=round(total_amount_collected, 3),
                              EXCEL_ENGINE=EXCEL_ENGINE,
                              bookings_closed=BOOKINGS_CLOSED)
    except Exception as e:
        import traceback
        print(f"Error in admin route: {e}")
        return render_template('error.html', error=f"Admin page error: {str(e)}")

@app.route('/toggle_bookings', methods=['POST'])
def toggle_bookings():
    try:
        submitted_password = request.form.get('password')
        
        correct_password = os.environ.get('DELETE_PASS')
        
        # Verify the password
        if not submitted_password or submitted_password != correct_password:
            return render_template('error.html', error="Incorrect password. Booking status not changed.")
        
        # If password is correct, proceed with toggling bookings status
        global BOOKINGS_CLOSED
        BOOKINGS_CLOSED = not BOOKINGS_CLOSED
        status = "closed" if BOOKINGS_CLOSED else "open"
        print(f"Bookings status changed to: {status}")
            
        return redirect(url_for('admin'))
    except Exception as e:
        print(f"Error toggling bookings status: {str(e)}")
        return render_template('error.html', error=f"Could not change bookings status: {str(e)}")

@app.route('/clear_orders', methods=['POST'])
def clear_orders():
    try:
        submitted_password = request.form.get('password')
        
        correct_password = os.environ.get('DELETE_PASS')
        
        # Verify the password
        if not submitted_password or submitted_password != correct_password:
            return render_template('error.html', error="Incorrect password. Orders were not cleared.")
        
        # If password is correct, proceed with clearing orders
        response = supabase.table('order-list').select('order_id').execute()
        order_ids = [order['order_id'] for order in response.data if isinstance(order, dict) and 'order_id' in order]
        
        if order_ids:
            print(f"Deleting orders with IDs: {order_ids}")
            for order_id in order_ids:
                delete_response = supabase.table('order-list').delete().eq('order_id', order_id).execute()
                print(f"Deleted order {order_id}: {delete_response}")
        else:
            print("No orders found to delete")
            
        return redirect(url_for('admin'))
    except Exception as e:
        print(f"Error clearing orders: {str(e)}")
        return render_template('error.html', error=f"Could not clear orders: {str(e)}")

@app.route('/delete_order', methods=['POST'])
def delete_order():
    try:
        order_id = request.form.get('order_id')
        submitted_password = request.form.get('password')
        
        correct_password = os.environ.get('DELETE_PASS')
        
        # Verify the password
        if not submitted_password or submitted_password != correct_password:
            return render_template('error.html', error="Incorrect password. The order was not deleted.")
        
        # If password is correct, proceed with deleting the order
        if order_id:
            print(f"Deleting order with ID: {order_id}")
            delete_response = supabase.table('order-list').delete().eq('order_id', order_id).execute()
            print(f"Deleted order {order_id}: {delete_response}")
            
            return redirect(url_for('admin'))
        else:
            return render_template('error.html', error="No order ID provided.")
    except Exception as e:
        print(f"Error deleting order: {str(e)}")
        return render_template('error.html', error=f"Could not delete order: {str(e)}")

@app.route('/export_excel')
def export_excel():
    """Export all orders as Excel file"""
    try:
        # Check if pandas and an Excel engine are available
        if pd is None or EXCEL_ENGINE is None:
            return render_template('error.html', 
                              error="Excel export not available",
                              details="Required libraries are not installed. Please run: pip install pandas xlsxwriter or pip install pandas openpyxl")
        
        response = supabase.table('order-list').select('*').execute()
        orders = response.data
        
        food_items_response = supabase.table('food-items').select('*').execute()
        food_items = food_items_response.data
        
        price_lookup = {}
        for item in food_items:
            price_lookup[item['name']] = item.get('price', 0)
        
        # Process orders
        order_data = []
        item_summary = {}
        total_amount_collected = 0
        
        for order in orders:
            if isinstance(order, dict):
                order_total = 0
                
                if 'item' in order and isinstance(order['item'], str) and '(x' in order['item']:
                    items_string = order['item']
                    items_parts = items_string.split(', ')
                    
                    for item_part in items_parts:
                        if '(x' in item_part:
                            item_name_parts = item_part.split(' (x')
                            if len(item_name_parts) >= 2:
                                item_name = item_name_parts[0]
                                try:
                                    quantity_str = item_name_parts[1].rstrip(')')
                                    quantity = int(quantity_str)
                                except (ValueError, IndexError):
                                    quantity = 1
                                
                                # Update order total and item summary
                                item_price = price_lookup.get(item_name, 0)
                                order_total += item_price * quantity
                                
                                if item_name in item_summary:
                                    item_summary[item_name] += quantity
                                else:
                                    item_summary[item_name] = quantity
                
                elif 'item' in order and not isinstance(order['item'], list):
                    item_name = str(order['item'])
                    try:
                        quantity = int(order.get('quantity', 1))
                    except (ValueError, TypeError):
                        quantity = 1
                    
                    # Update order total and item summary
                    item_price = price_lookup.get(item_name, 0)
                    order_total += item_price * quantity
                        
                    # Update summary
                    if item_name in item_summary:
                        item_summary[item_name] += quantity
                    else:
                        item_summary[item_name] = quantity
                
                # Add total amount to the order
                order['total_amount'] = round(order_total, 3)
                total_amount_collected += order_total
                
                # Format the order data for Excel
                order_dict = {
                    'Order ID': order.get('order_id', ''),
                    'Customer Name': order.get('customer_name', ''),
                    'Phone': order.get('phone', ''),
                    'Items': order.get('item', ''),
                    'Quantity': order.get('quantity', ''),
                    'Membership': order.get('membership', ''),
                    'Total Amount (KD)': order.get('total_amount', 0)
                }
                order_data.append(order_dict)
        
        # Create summary data
        summary_data = [{"Item": item, "Quantity Ordered": qty} for item, qty in item_summary.items()]
        summary_data.append({"Item": "TOTAL", "Quantity Ordered": sum(item_summary.values())})
        
        # Create Excel file with multiple sheets
        today = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        output = BytesIO()
        
        # Create DataFrame for orders
        df_orders = pd.DataFrame(order_data) if order_data else pd.DataFrame({'No Data': ['No orders found']})
        
        # Create DataFrame for summary
        df_summary = pd.DataFrame(summary_data) if summary_data else pd.DataFrame({'No Data': ['No items ordered']})
        
        # Create Excel writer with the available engine
        with pd.ExcelWriter(output, engine=EXCEL_ENGINE) as writer:
            df_orders.to_excel(writer, sheet_name='Orders', index=False)
            df_summary.to_excel(writer, sheet_name='Summary', index=False)
            
            # Only apply formatting if xlsxwriter is the engine
            if EXCEL_ENGINE == 'xlsxwriter':
                try:
                    # Format the Orders sheet
                    workbook = writer.book
                    worksheet = writer.sheets['Orders']
                    header_format = workbook.add_format({'bold': True, 'bg_color': '#333333', 'font_color': 'white'})
                    
                    for col_num, value in enumerate(df_orders.columns.values):
                        worksheet.write(0, col_num, value, header_format)
                    
                    # Format the Summary sheet
                    worksheet = writer.sheets['Summary']
                    total_format = workbook.add_format({'bold': True, 'bg_color': '#DDDDDD'})
                    
                    for col_num, value in enumerate(df_summary.columns.values):
                        worksheet.write(0, col_num, value, header_format)
                    
                    # Format total row
                    if len(summary_data) > 0:
                        last_row = len(summary_data)
                        worksheet.set_row(last_row, None, total_format)
                except Exception as format_error:
                    print(f"Warning: Could not apply Excel formatting: {str(format_error)}")
        
        output.seek(0)
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            download_name=f'orders_export_{today}.xlsx',
            as_attachment=True
        )
        
    except Exception as e:
        import traceback
        print(f"Error exporting Excel: {str(e)}")
        print(traceback.format_exc())
        
        # If the error is specifically about missing xlsxwriter, give a helpful message
        if "No module named 'xlsxwriter'" in str(e):
            return render_template('error.html', 
                                error="Excel export failed - Missing dependency",
                                details="The xlsxwriter module is not installed. Please run: pip install xlsxwriter")
        
        return render_template('error.html', 
                              error=f"Failed to export data: {str(e)}",
                              details="There was an error processing your request.")

@app.route('/bookings_closed')
def bookings_closed():
    return render_template('bookings_closed.html')

@app.template_filter('datetime')
def format_datetime(timestamp):
    from datetime import datetime, timedelta
    try:
        # Convert timestamp to datetime in UTC and add 3 hours for Arabian Standard Time (UTC+3)
        dt_utc = datetime.utcfromtimestamp(timestamp)
        dt_ast = dt_utc + timedelta(hours=3)
        return dt_ast.strftime('%Y-%m-%d %H:%M:%S')
    
    except (ValueError, TypeError):
        return 'Invalid timestamp'

# Add a custom template filter to convert Python boolean to string for JavaScript
@app.template_filter('to_js_bool')
def to_js_bool(value):
    return str(bool(value))

if __name__ == '__main__':        
    app.run(debug=False)