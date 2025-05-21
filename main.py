from flask import Flask, render_template, request, redirect, url_for, jsonify, session
import os
from supabase import create_client
import uuid
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

SUPABASE_URL = os.environ.get('SUPA_URL')
SUPABASE_KEY = os.environ.get('SUPA_KEY')


supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialize cart in session
@app.before_request
def before_request():
    if 'cart' not in session:
        session['cart'] = {}

@app.route('/')
def home():
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
                'item': items_text,  # All items as a concatenated string
                'quantity': total_quantity,  # Sum of all quantities (as an integer)
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
        print(traceback.format_exc())  # Print full stack trace
        # Show an error page
        return render_template('error.html', error=f"Order processing error: {str(e)}")

@app.route('/admin')
def admin():
    try:
        # Get all active orders
        response = supabase.table('order-list').select('*').execute()
        orders = response.data
        
        item_summary = {}
        
        # Process orders
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
                        
                    # Update summary
                    if item_name in item_summary:
                        item_summary[item_name] += quantity
                    else:
                        item_summary[item_name] = quantity
        
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
        
        return render_template('admin.html', orders=parsed_orders, item_summary=sorted_summary)
    except Exception as e:
        import traceback
        print(f"Error in admin route: {e}")
        print(traceback.format_exc())  # Print full traceback for debugging
        return render_template('error.html', error=f"Admin page error: {str(e)}")

@app.route('/clear_orders', methods=['POST'])
def clear_orders():
    try:
        # Get the password from the form
        submitted_password = request.form.get('password')
        
        # Get the correct password from environment variables
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
        # Get the order ID and password from the form
        order_id = request.form.get('order_id')
        submitted_password = request.form.get('password')
        
        # Get the correct password from environment variables
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

if __name__ == '__main__':
    # Add a custom template filter to convert Python boolean to string for JavaScript
    @app.template_filter('to_js_bool')
    def to_js_bool(value):
        return str(bool(value))
        
    app.run(debug=False)